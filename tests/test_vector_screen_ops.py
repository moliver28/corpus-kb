"""Unit tests for the vector-space loader and the vector screen ops.

No Postgres anywhere: the storage accessor is monkeypatched at the
module seams (``vectors._read_stored``, ``vector_screen_tools
._load_vectors``). Determinism asserts byte-equal envelopes across
repeated runs (the program's numpy-exact rule).
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest

from corpus_kb.analysis import vectors
from corpus_kb.errors import ValidationError
from corpus_kb.tools import vector_screen_tools

# ---------------------------------------------------------------------------
# Fakes


class _Row:
    """Minimal shape of one classification-engine ``StoredVector`` row."""

    def __init__(self, chunk_id: str, vector: list[float]) -> None:
        self.chunk_id = chunk_id
        self.text = "text " + chunk_id
        self.vector = np.asarray(vector, dtype=np.float32)


def _stored(rows: list[_Row]) -> list[_Row]:
    return rows


def _space(ids: list[str], rows: list[list[float]], excluded: int = 0) -> vectors.VectorSpace:
    matrix = np.asarray(rows, dtype=np.float64) if rows else np.zeros((0, 0))
    return vectors.VectorSpace(ids=ids, matrix=matrix, excluded_zero_count=excluded)


async def _patch_stored(monkeypatch: pytest.MonkeyPatch, rows: list[_Row]) -> None:
    async def fake_read(pg_pool: object, tenant_id: str) -> list[_Row]:
        return rows

    monkeypatch.setattr(vectors, "_read_stored", fake_read)


# ---------------------------------------------------------------------------
# analysis.vectors.load_tenant_vectors


async def test_load_vectors_orders_ids_excludes_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _Row("c2", [3.0, 4.0]),
        _Row("c1", [1.0, 0.0]),
        _Row("c3", [0.0, 0.0]),  # zero: no direction
        _Row("c4", [0.0, 1e-13]),  # norm below the epsilon pin: excluded
        _Row("c5", [0.0, 1e-9]),  # tiny but above the pin: kept
    ]
    await _patch_stored(monkeypatch, rows)
    space = await vectors.load_tenant_vectors(pg_pool=None, tenant_id="t", config=None)
    assert space.ids == ["c1", "c2", "c5"]
    assert space.matrix.dtype == np.float64
    assert space.matrix.shape == (3, 2)
    assert space.excluded_zero_count == 2


async def test_load_vectors_rejects_mixed_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_Row("a", [1.0, 2.0]), _Row("b", [1.0, 2.0, 3.0])]
    await _patch_stored(monkeypatch, rows)
    with pytest.raises(ValidationError) as excinfo:
        await vectors.load_tenant_vectors(None, "t", None)
    assert "mixed dimensions" in str(excinfo.value)
    assert excinfo.value.remediation


async def test_load_vectors_empty_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    await _patch_stored(monkeypatch, [])
    space = await vectors.load_tenant_vectors(None, "t", None)
    assert space.ids == []
    assert space.matrix.shape == (0, 0)
    assert space.excluded_zero_count == 0


def test_unit_rows_are_unit_length() -> None:
    matrix = np.asarray([[3.0, 4.0], [0.0, 0.0]])
    units = vectors.unit_rows(matrix)
    assert units[0].tolist() == pytest.approx([0.6, 0.8])
    assert units[1].tolist() == [0.0, 0.0]


# ---------------------------------------------------------------------------
# duplicates_screen


def _patch_screen_vectors(monkeypatch: pytest.MonkeyPatch, space: vectors.VectorSpace) -> None:
    async def fake_load(pg_pool: object, tenant_id: str, config: Any) -> vectors.VectorSpace:
        return space

    monkeypatch.setattr(vector_screen_tools, "_load_vectors", fake_load)


async def test_duplicates_screen_orders_filters_and_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a-b near-identical (0.99+), a-c moderate, d unrelated.
    space = _space(
        ["a", "b", "c", "d"],
        [[1.0, 0.0], [0.995, 0.0999], [0.98, 0.199], [0.0, 1.0]],
    )
    _patch_screen_vectors(monkeypatch, space)
    out = await vector_screen_tools.duplicates_screen(None, None, "t", threshold=0.98, limit=10)
    assert out["status"] == "success"
    assert out["screened_count"] == 4
    assert out["threshold"] == 0.98
    sims = [pair["similarity"] for pair in out["pairs"]]
    assert sims == sorted(sims, reverse=True)
    for pair in out["pairs"]:
        assert pair["chunk_id_i"] < pair["chunk_id_j"]
        assert pair["similarity"] >= 0.98


async def test_duplicates_screen_limit_caps_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(["a", "b", "c"], [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    _patch_screen_vectors(monkeypatch, space)
    out = await vector_screen_tools.duplicates_screen(None, None, "t", threshold=0.9, limit=1)
    assert len(out["pairs"]) == 1
    assert out["pairs"][0]["chunk_id_i"] == "a"
    assert out["pairs"][0]["chunk_id_j"] == "b"


async def test_duplicates_screen_no_pairs_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(["a", "b"], [[1.0, 0.0], [0.0, 1.0]])
    _patch_screen_vectors(monkeypatch, space)
    out = await vector_screen_tools.duplicates_screen(None, None, "t", threshold=0.99, limit=10)
    assert out == {
        "status": "success",
        "pairs": [],
        "threshold": 0.99,
        "screened_count": 2,
    }


async def test_duplicates_screen_empty_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_screen_vectors(monkeypatch, _space([], []))
    out = await vector_screen_tools.duplicates_screen(None, None, "t")
    assert out["status"] == "empty"
    assert out["reason"] and out["remedy"]
    assert out["pairs"] == []
    assert out["screened_count"] == 0


async def test_duplicates_screen_rejects_bad_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        await vector_screen_tools.duplicates_screen(None, None, "t", threshold=1.5)
    with pytest.raises(ValidationError):
        await vector_screen_tools.duplicates_screen(None, None, "t", limit=0)


async def test_duplicates_screen_is_byte_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        [1.0, 0.1, 0.0],
        [0.9, 0.2, 0.1],
        [0.0, 1.0, 0.2],
        [0.05, 0.95, 0.2],
        [1.0, 0.0, 0.3],
    ]
    ids = ["e", "d", "c", "b", "a"]
    _patch_screen_vectors(monkeypatch, _space(ids, rows))
    first = await vector_screen_tools.duplicates_screen(None, None, "t", threshold=0.9)
    second = await vector_screen_tools.duplicates_screen(None, None, "t", threshold=0.9)
    assert first == second


# ---------------------------------------------------------------------------
# outliers_rank


async def test_outliers_rank_orders_by_distance(monkeypatch: pytest.MonkeyPatch) -> None:
    # z is an isolated point; the a-b-c cluster is tight, so z's kNN
    # distance (1.0) is the largest and it ranks first at k=1.
    space = _space(
        ["a", "b", "c", "z"],
        [[1.0, 0.0], [1.0, 0.01], [0.99, 0.02], [0.0, 1.0]],
    )
    _patch_screen_vectors(monkeypatch, space)
    out = await vector_screen_tools.outliers_rank(None, None, "t", k=1, limit=10)
    assert out["status"] == "success"
    assert out["screened_count"] == 4
    top = out["outliers"][0]
    assert top["chunk_id"] == "z"
    distances = [row["knn_distance"] for row in out["outliers"]]
    assert distances == sorted(distances, reverse=True)


async def test_outliers_rank_ties_break_by_chunk_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # Collinear nonzero vectors: unit rows are identical, every pairwise
    # similarity is 1, every kNN distance is 0 — pure tie, so order must
    # be chunk_id ascending. (A zero vector here would be unrealistic:
    # the real loader excludes zero vectors before this op runs.)
    space = _space(["p2", "p1", "p3"], [[0.5], [1.0], [2.0]])
    _patch_screen_vectors(monkeypatch, space)
    out = await vector_screen_tools.outliers_rank(None, None, "t", k=1, limit=10)
    assert [row["chunk_id"] for row in out["outliers"]] == ["p1", "p2", "p3"]


async def test_outliers_rank_k_clamps_to_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(["a", "b"], [[1.0, 0.0], [0.0, 1.0]])
    _patch_screen_vectors(monkeypatch, space)
    out = await vector_screen_tools.outliers_rank(None, None, "t", k=50, limit=10)
    assert out["status"] == "success"
    assert len(out["outliers"]) == 2


async def test_outliers_rank_single_chunk_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(["a"], [[1.0, 0.0]])
    _patch_screen_vectors(monkeypatch, space)
    out = await vector_screen_tools.outliers_rank(None, None, "t")
    assert out["status"] == "empty"
    assert out["reason"] and out["remedy"]
    assert out["outliers"] == []


async def test_outliers_rank_rejects_bad_k(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        await vector_screen_tools.outliers_rank(None, None, "t", k=0)
    with pytest.raises(ValidationError):
        await vector_screen_tools.outliers_rank(None, None, "t", limit=0)


async def test_outliers_rank_is_byte_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [[1.0, 0.0], [0.8, 0.6], [0.0, 1.0], [-0.6, 0.8], [0.9, 0.1]]
    ids = ["e", "d", "c", "b", "a"]
    _patch_screen_vectors(monkeypatch, _space(ids, rows))
    first = await vector_screen_tools.outliers_rank(None, None, "t", k=2)
    second = await vector_screen_tools.outliers_rank(None, None, "t", k=2)
    assert first == second


async def test_outliers_rank_k_at_corpus_minus_one_stays_json_finite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # k = total - 1 must never leak the self-exclusion marker into the
    # envelope (a non-JSON inf on tiny corpora).
    space = _space(["a", "b"], [[1.0, 0.0], [0.6, 0.8]])
    _patch_screen_vectors(monkeypatch, space)
    out = await vector_screen_tools.outliers_rank(None, None, "t", k=1)
    assert out["status"] == "success"
    for row in out["outliers"]:
        assert row["knn_distance"] == pytest.approx(row["knn_distance"], abs=1e-9)
        assert row["knn_distance"] == json.loads(json.dumps(row["knn_distance"]))


async def test_outliers_rank_k_above_corpus_shrinks_and_stays_finite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    space = _space(["a", "b", "c"], [[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]])
    _patch_screen_vectors(monkeypatch, space)
    out = await vector_screen_tools.outliers_rank(None, None, "t", k=99)
    assert out["status"] == "success"
    assert len(out["outliers"]) == 3
    for row in out["outliers"]:
        assert -1.0 <= row["knn_distance"] <= 2.0
