"""Unit tests for neighbors_classify and centroids_classify.

No Postgres: the vector and label accessors are monkeypatched at the
module seams (``_load_vectors``/``_load_labels``). The geometry is
hand-checked 2-D data so every expectation is computable by hand.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from corpus_kb.analysis import vectors
from corpus_kb.errors import ValidationError
from corpus_kb.tools import centroid_tools, neighbor_tools

# ---------------------------------------------------------------------------
# Fakes


def _space(rows: dict[str, list[float]]) -> vectors.VectorSpace:
    ids = sorted(rows)
    return vectors.VectorSpace(
        ids=ids,
        matrix=np.asarray([rows[i] for i in ids], dtype=np.float64),
        excluded_zero_count=0,
    )


def _labels(pairs: list[tuple[str, str]]) -> list[dict[str, object]]:
    return [{"chunk_id": chunk_id, "label": label} for chunk_id, label in pairs]


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    space: vectors.VectorSpace,
    labels: list[dict[str, object]],
) -> None:
    async def fake_vectors(pg_pool: object, tenant_id: str, config: Any) -> vectors.VectorSpace:
        return space

    async def fake_labels(pg_pool: object, tenant_id: str) -> list[dict[str, object]]:
        return labels

    monkeypatch.setattr(module, "_load_vectors", fake_vectors)
    monkeypatch.setattr(module, "_load_labels", fake_labels)


NEIGHBOR_ROWS: dict[str, list[float]] = {
    "l-a": [1.0, 0.0],
    "l-b": [1.0, 0.1],
    "l-c": [0.0, 1.0],
    "u1": [0.0, 1.1],
    "u2": [1.0, 1.0],
}
NEIGHBOR_LABELS = _labels([("l-a", "x"), ("l-b", "x"), ("l-c", "y")])


# ---------------------------------------------------------------------------
# neighbors_classify


async def test_neighbors_predicts_nearest_label(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, neighbor_tools, _space(NEIGHBOR_ROWS), NEIGHBOR_LABELS)
    out = await neighbor_tools.neighbors_classify(None, None, "t", k=1)
    assert out["status"] == "success"
    assert out["screened_count"] == 5
    rows = {row["chunk_id"]: row for row in out["predictions"]}
    # u1 sits on the y axis next to l-c.
    assert rows["u1"]["label"] == "y"
    assert rows["u1"]["neighbor_ids"] == ["l-c"]
    # Only unlabeled chunks are predicted by default, sorted by id.
    assert [row["chunk_id"] for row in out["predictions"]] == ["u1", "u2"]


async def test_neighbors_majority_vote(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, neighbor_tools, _space(NEIGHBOR_ROWS), NEIGHBOR_LABELS)
    out = await neighbor_tools.neighbors_classify(None, None, "t", k=3)
    rows = {row["chunk_id"]: row for row in out["predictions"]}
    # u2 = [1,1]: nearest l-b (x), then l-a (x) and l-c (y) tied on
    # similarity with l-a winning the id tiebreak. Votes: x=2, y=1.
    row = rows["u2"]
    assert row["label"] == "x"
    assert row["votes"] == [
        {"label": "x", "votes": 2},
        {"label": "y", "votes": 1},
    ]
    assert row["neighbor_ids"] == ["l-b", "l-a", "l-c"]


async def test_neighbors_vote_tie_goes_alphabetical(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = {"p1": [1.0, 0.0], "p2": [0.0, 1.0], "t1": [1.0, 1.0]}
    labels = _labels([("p1", "y"), ("p2", "x")])
    _patch(monkeypatch, neighbor_tools, _space(rows), labels)
    out = await neighbor_tools.neighbors_classify(None, None, "t", k=2)
    row = out["predictions"][0]
    # One vote each; "x" < "y" alphabetically.
    assert row["label"] == "x"


async def test_neighbors_explicit_targets_exclude_self(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, neighbor_tools, _space(NEIGHBOR_ROWS), NEIGHBOR_LABELS)
    out = await neighbor_tools.neighbors_classify(None, None, "t", k=2, chunk_ids=["l-b", "nope"])
    rows = {row["chunk_id"]: row for row in out["predictions"]}
    assert list(rows) == ["l-b"]  # unknown ids are skipped
    assert "l-b" not in rows["l-b"]["neighbor_ids"]
    assert rows["l-b"]["label"] == "x"


async def test_neighbors_no_labels_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, neighbor_tools, _space(NEIGHBOR_ROWS), [])
    out = await neighbor_tools.neighbors_classify(None, None, "t")
    assert out["status"] == "empty"
    assert out["reason"] and out["remedy"]
    assert out["predictions"] == []
    assert out["screened_count"] == 5


async def test_neighbors_requested_ids_all_missing_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, neighbor_tools, _space(NEIGHBOR_ROWS), NEIGHBOR_LABELS)
    out = await neighbor_tools.neighbors_classify(None, None, "t", chunk_ids=["ghost"])
    assert out["status"] == "empty"
    assert out["predictions"] == []


async def test_neighbors_rejects_bad_k(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        await neighbor_tools.neighbors_classify(None, None, "t", k=0)


async def test_neighbors_is_byte_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, neighbor_tools, _space(NEIGHBOR_ROWS), NEIGHBOR_LABELS)
    first = await neighbor_tools.neighbors_classify(None, None, "t", k=2)
    second = await neighbor_tools.neighbors_classify(None, None, "t", k=2)
    assert first == second


# ---------------------------------------------------------------------------
# centroids_classify


CENTROID_ROWS: dict[str, list[float]] = {
    "x1": [1.0, 0.0],
    "x2": [1.0, 0.2],
    "y1": [0.0, 1.0],
    "y2": [0.2, 1.0],
    "near-x": [1.0, 0.1],
    "near-y": [0.1, 1.0],
    "middle": [0.7, 0.7],
}
CENTROID_LABELS = _labels([("x1", "x"), ("x2", "x"), ("y1", "y"), ("y2", "y")])


async def test_centroids_predicts_top_label(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, centroid_tools, _space(CENTROID_ROWS), CENTROID_LABELS)
    out = await centroid_tools.centroids_classify(None, None, "t")
    assert out["status"] == "success"
    assert out["screened_count"] == 7
    rows = {row["chunk_id"]: row for row in out["predictions"]}
    assert rows["near-x"]["label"] == "x"
    assert rows["near-x"]["abstained"] is False
    assert rows["near-y"]["label"] == "y"


async def test_centroids_abstains_inside_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, centroid_tools, _space(CENTROID_ROWS), CENTROID_LABELS)
    out = await centroid_tools.centroids_classify(None, None, "t", abstain_margin=0.5)
    rows = {row["chunk_id"]: row for row in out["predictions"]}
    assert rows["middle"]["abstained"] is True
    assert rows["middle"]["margin"] < 0.5
    assert rows["near-x"]["abstained"] is False


async def test_centroids_single_label_never_abstains(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        centroid_tools,
        _space(CENTROID_ROWS),
        _labels([("x1", "only"), ("x2", "only")]),
    )
    out = await centroid_tools.centroids_classify(None, None, "t")
    for row in out["predictions"]:
        assert row["abstained"] is False
        assert row["label"] == "only"


async def test_centroids_no_labels_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, centroid_tools, _space(CENTROID_ROWS), [])
    out = await centroid_tools.centroids_classify(None, None, "t")
    assert out["status"] == "empty"
    assert out["reason"] and out["remedy"]
    assert out["predictions"] == []
    assert out["screened_count"] == 7


async def test_centroids_rejects_bad_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, centroid_tools, _space(CENTROID_ROWS), CENTROID_LABELS)
    with pytest.raises(ValidationError):
        await centroid_tools.centroids_classify(None, None, "t", abstain_margin=-0.1)
    with pytest.raises(ValidationError):
        await centroid_tools.centroids_classify(None, None, "t", abstain_margin=2.5)


async def test_centroids_is_byte_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, centroid_tools, _space(CENTROID_ROWS), CENTROID_LABELS)
    first = await centroid_tools.centroids_classify(None, None, "t")
    second = await centroid_tools.centroids_classify(None, None, "t")
    assert first == second
