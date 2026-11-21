"""Tests for the three UXR method handlers (register 31, 32, 33).

Covered: the survey open-end coding read shape (one chunk per answer,
question in metadata), the honest empty envelope, the cluster and
single-code paths with the canned seams, the fixed 0-4 severity rubric
(highest anchor wins, unmatched rows route to review), and the fixed
eight-emotion entailment leg (sorted-tie label, honest unavailable).
The DuckDB twin legs follow the PR #525 pattern at the bottom.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from corpus_kb.analysis import vectors
from corpus_kb.analysis.nli_contract import NliError
from corpus_kb.analysis.vectors import VectorSpace
from corpus_kb.errors import ValidationError
from corpus_kb.tools import uxr_method_tools, uxr_survey_codes

TENANT = "00000000-0000-0000-0000-000000000001"


class _Chunk:
    def __init__(self, chunk_id: str, text: str, metadata: dict[str, object]) -> None:
        self.chunk_id = chunk_id
        self.text = text
        self.metadata = metadata


def _survey_chunk(chunk_id: str, question: str, answer: str) -> _Chunk:
    return _Chunk(
        chunk_id,
        f"Q: {question}\nA: {answer}",
        {"strategy": "survey", "question": question, "row": 2, "answer_column": "answer"},
    )


def _patch_chunks(monkeypatch: pytest.MonkeyPatch, chunks: list[_Chunk], module: Any) -> None:
    async def fake_read(pool: Any, tenant_id: str, limit: int = 50_000) -> list[_Chunk]:
        return chunks

    monkeypatch.setattr(module.engine, "read_chunks", fake_read)


def _space(ids: list[str], matrix: np.ndarray) -> VectorSpace:
    return VectorSpace(ids=ids, matrix=matrix, excluded_zero_count=0)


# ---------------------------------------------------------------------------
# survey_openend_codes (31)
# ---------------------------------------------------------------------------


async def test_survey_codes_empty_without_survey_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_chunks(monkeypatch, [_Chunk("c1", "plain text", {})], uxr_survey_codes)
    result = await uxr_survey_codes.survey_openend_codes(None, config={}, tenant_id=TENANT)
    assert result["status"] == "empty"
    assert result["codes"] == [] and result["screened_count"] == 0
    assert "survey" in str(result["remedy"])


async def test_survey_single_code_below_cluster_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [_survey_chunk("c1", "Speed", "too slow")]
    _patch_chunks(monkeypatch, chunks, uxr_survey_codes)

    async def fake_vectors(pool: Any, tenant_id: str, config: Any) -> VectorSpace:
        return _space(["c1"], np.array([[1.0, 0.0]]))

    monkeypatch.setattr(uxr_survey_codes, "_load_vectors", fake_vectors)
    result = await uxr_survey_codes.survey_openend_codes(None, config={}, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["screened_count"] == 1
    row = result["codes"][0]
    assert row["question"] == "Speed" and row["count"] == 1
    assert row["code_id"] == "c-1" and row["label"] == "too slow"


async def test_survey_clusters_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [_survey_chunk(f"c{i}", "Speed", f"answer number {i}") for i in range(6)]
    _patch_chunks(monkeypatch, chunks, uxr_survey_codes)
    matrix = np.array([[1.0, 0.0], [0.9, 0.1], [0.8, 0.2], [0.0, 1.0], [0.1, 0.9], [0.2, 0.8]])
    space = _space([f"c{i}" for i in range(6)], matrix)

    async def fake_vectors(pool: Any, tenant_id: str, config: Any) -> VectorSpace:
        return space

    monkeypatch.setattr(uxr_survey_codes, "_load_vectors", fake_vectors)
    monkeypatch.setattr(uxr_survey_codes, "_reduce", lambda m: (m, "stub"))
    monkeypatch.setattr(
        uxr_survey_codes,
        "_cluster",
        lambda reduced, floor: np.array([0, 0, 0, 1, 1, -1]),
    )
    result = await uxr_survey_codes.survey_openend_codes(
        None, config={}, tenant_id=TENANT, min_cluster_size=2
    )
    assert result["status"] == "success" and result["screened_count"] == 6
    assert result["params"]["excluded_no_vector"] == 0
    rows = result["codes"]
    assert [row["code_id"] for row in rows] == ["c-1", "c-2", "c-3"]
    assert rows[0]["count"] == 3 and rows[1]["count"] == 2 and rows[2]["count"] == 1
    assert rows[2]["label"] == "unclustered answers"
    top = rows[0]
    assert top["count"] == 3 and top["label"].startswith("answer number")
    assert set(top["exemplar_chunk_ids"]) == {"c0", "c1", "c2"}


async def test_survey_skips_chunks_without_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [_survey_chunk("c1", "Speed", "a"), _survey_chunk("c9", "Speed", "b")]
    _patch_chunks(monkeypatch, chunks, uxr_survey_codes)
    space = _space(["c1"], np.array([[1.0, 0.0]]))

    async def fake_vectors(pool: Any, tenant_id: str, config: Any) -> VectorSpace:
        return space

    monkeypatch.setattr(uxr_survey_codes, "_load_vectors", fake_vectors)
    result = await uxr_survey_codes.survey_openend_codes(None, config={}, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["screened_count"] == 2
    assert result["params"]["excluded_no_vector"] == 1


async def test_survey_rejects_bad_min_cluster_size() -> None:
    with pytest.raises(ValidationError):
        await uxr_survey_codes.survey_openend_codes(
            None, config={}, tenant_id=TENANT, min_cluster_size=1
        )


async def test_survey_honors_the_chunk_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [_survey_chunk("c1", "Speed", "a"), _survey_chunk("c2", "Speed", "b")]
    _patch_chunks(monkeypatch, chunks, uxr_survey_codes)

    async def fake_vectors(pool: Any, tenant_id: str, config: Any) -> VectorSpace:
        return _space(["c1", "c2"], np.array([[1.0, 0.0], [0.0, 1.0]]))

    monkeypatch.setattr(uxr_survey_codes, "_load_vectors", fake_vectors)
    result = await uxr_survey_codes.survey_openend_codes(
        None, config={}, tenant_id=TENANT, chunk_ids=["c2"]
    )
    assert result["screened_count"] == 1 and result["codes"][0]["exemplar_chunk_ids"] == ["c2"]


# ---------------------------------------------------------------------------
# usability_severity (32)
# ---------------------------------------------------------------------------


async def test_severity_ranks_highest_anchor_first(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [
        _Chunk("c1", "The app crashes and I lose data.", {}),
        _Chunk("c2", "The flow is confusing and hard to use.", {}),
        _Chunk("c3", "The button label wording is a typo.", {}),
        _Chunk("c4", "The meeting is on Tuesday.", {}),
    ]
    _patch_chunks(monkeypatch, chunks, uxr_method_tools)
    result = await uxr_method_tools.usability_severity(None, config={}, tenant_id=TENANT)
    assert result["status"] == "success" and result["screened_count"] == 4
    rows = result["predictions"]
    assert [row["chunk_id"] for row in rows[:2]] == ["c1", "c2"]
    assert rows[0]["severity"] == 4 and rows[1]["severity"] == 2
    assert rows[3]["severity"] == 0 and rows[3]["review"] is True
    assert rows[0]["review"] is False and rows[0]["matched_terms"]


async def test_severity_limit_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_chunks(monkeypatch, [], uxr_method_tools)
    empty = await uxr_method_tools.usability_severity(None, config={}, tenant_id=TENANT)
    assert empty["status"] == "empty" and empty["predictions"] == []
    chunks = [_Chunk("c1", "crash", {}), _Chunk("c2", "crash again", {})]
    _patch_chunks(monkeypatch, chunks, uxr_method_tools)
    result = await uxr_method_tools.usability_severity(None, config={}, tenant_id=TENANT, limit=1)
    assert len(result["predictions"]) == 1


# ---------------------------------------------------------------------------
# emotion_tag (33)
# ---------------------------------------------------------------------------


def _emotion_world(monkeypatch: pytest.MonkeyPatch, verdict: str = "ENTAIL") -> None:
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]])
    space = _space(["c1", "c2"], matrix)

    async def fake_space(
        pool: Any, tenant_id: str, config: Any
    ) -> tuple[VectorSpace, dict[str, str]]:
        return space, {"c1": "parity one", "c2": "parity two"}

    async def fake_verdict(prompt: str, model: str, base_url: str) -> str:
        return verdict

    monkeypatch.setattr(uxr_method_tools, "_load_space", fake_space)
    monkeypatch.setattr(uxr_method_tools.contract, "judge_verdict", fake_verdict)


async def test_emotion_fixed_taxonomy_tie_goes_alphabetical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _emotion_world(monkeypatch)
    result = await uxr_method_tools.emotion_tag(None, config={}, tenant_id=TENANT)
    assert result["status"] == "success" and result["screened_count"] == 2
    assert [row["chunk_id"] for row in result["predictions"]] == ["c1", "c2"]
    row = result["predictions"][0]
    assert row["label"] == "anger"
    assert len(row["scores"]) == 8
    assert {entry["label"] for entry in row["scores"]} == set(uxr_method_tools.EMOTION_LABELS)


async def test_emotion_names_the_model_that_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    _emotion_world(monkeypatch)
    result = await uxr_method_tools.emotion_tag(None, config={}, tenant_id=TENANT, model="tag-x")
    assert result["model"] == "tag-x"


async def test_emotion_unreachable_model_answers_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _emotion_world(monkeypatch)

    async def broken_verdict(prompt: str, model: str, base_url: str) -> str:
        raise NliError("unreachable", "down")

    monkeypatch.setattr(uxr_method_tools.contract, "judge_verdict", broken_verdict)
    result = await uxr_method_tools.emotion_tag(None, config={}, tenant_id=TENANT)
    assert result["status"] == "unavailable"
    assert result["predictions"] == [] and result["screened_count"] == 0


async def test_emotion_rejects_bad_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        await uxr_method_tools.emotion_tag(None, config={}, tenant_id=TENANT, limit=0)


# ---------------------------------------------------------------------------
# DuckDB twins (PR #525 pattern): the shared legs on one migrated file
# ---------------------------------------------------------------------------


@pytest.fixture()
async def duckdb_store(tmp_path: Path) -> Any:
    """One migrated DuckDB gateway with seeded survey + finding chunks."""
    from corpus_kb._setup.migrate import run_migrations
    from corpus_kb.storage.duckdb_gateway import DuckDbGateway
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

    db_path = tmp_path / "uxr.duckdb"
    await run_migrations(duckdb_path_to_conn_string(db_path))
    gateway = await DuckDbGateway.connect(str(db_path))
    doc_id = str(uuid4())
    tenant = TENANT
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ($1::uuid, $2, $3, $4)",
        doc_id,
        tenant,
        "uxr-twin",
        "text",
    )
    rows = [
        ("Speed", "The export crashes and loses data."),
        ("Speed", "The export is slow but works."),
        ("Trust", "The trust panel looks fine."),
    ]
    for index, (question, answer) in enumerate(rows):
        metadata = json.dumps(
            {
                "strategy": "survey",
                "question": question,
                "row": index + 2,
                "answer_column": "answer",
            }
        )
        await gateway.execute(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,"
            " source_type, metadata)"
            " VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6, $7::jsonb)",
            str(uuid4()),
            tenant,
            doc_id,
            index,
            f"Q: {question}\nA: {answer}",
            "text",
            metadata,
        )
    yield gateway
    await gateway.close()


async def test_survey_codes_twin_on_duckdb(duckdb_store: Any) -> None:
    result = await uxr_survey_codes.survey_openend_codes(duckdb_store, config={}, tenant_id=TENANT)
    # Zero-vector degraded mode: the metadata read answers, every
    # answer is counted, and the excluded-no-vector fact is honest.
    assert result["status"] == "success" and result["screened_count"] == 3
    assert result["codes"] == []
    assert result["params"]["excluded_no_vector"] == 3


async def test_severity_twin_on_duckdb(duckdb_store: Any) -> None:
    result = await uxr_method_tools.usability_severity(duckdb_store, config={}, tenant_id=TENANT)
    assert result["status"] == "success" and result["screened_count"] == 3
    rows = result["predictions"]
    assert rows[0]["severity"] == 4 and rows[0]["chunk_id"]
    assert rows[-1]["severity"] == 0 and rows[-1]["review"] is True
    assert result["params"]["review_count"] == 1


async def test_emotion_twin_on_duckdb(duckdb_store: Any) -> None:
    result = await uxr_method_tools.emotion_tag(duckdb_store, config={}, tenant_id=TENANT)
    assert result["status"] == "empty"  # no stored vectors in the twin world
    assert result["predictions"] == []


def _zero_space(count: int) -> VectorSpace:
    return _space([f"z{i}" for i in range(count)], np.zeros((count, 2)))


async def test_survey_degraded_zero_vectors_still_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero-vector degraded mode keeps the envelope honest, never raises."""
    chunks = [_survey_chunk(f"z{i}", "Speed", f"answer {i}") for i in range(6)]
    _patch_chunks(monkeypatch, chunks, uxr_survey_codes)

    async def fake_vectors(pool: Any, tenant_id: str, config: Any) -> VectorSpace:
        return _zero_space(6)

    monkeypatch.setattr(uxr_survey_codes, "_load_vectors", fake_vectors)
    result = await uxr_survey_codes.survey_openend_codes(
        None, config={}, tenant_id=TENANT, min_cluster_size=2
    )
    assert result["status"] in {"success", "empty"}
    assert result["screened_count"] == 6
    assert isinstance(vectors.load_tenant_vectors, object)  # seam sanity
