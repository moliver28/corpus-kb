"""The lexical classifier op: TF-IDF features + logistic head over stored labels.

Real sklearn runs the fit (the venv always has it); storage sits behind
the three seams (``lexical_tools.writer`` tables probe and label read,
``lexical_tools.engine`` chunk read). Covered: the honest statuses
(pre-migration, no stored labels, one class, thin classes, unresolved
ids), the sorted-order envelope, the screening denominator, and
6-place probabilities.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.tools import lexical_tools

TENANT = "00000000-0000-0000-0000-000000000001"

CAT_TEXTS = [
    "the cat sat on the mat",
    "cats chase mice in the garden",
    "the kitten played with yarn",
]
CAR_TEXTS = [
    "the car drove on the road",
    "cars need fuel and oil",
    "the driver parked the vehicle",
]
EXTRA_TEXTS = [
    "the kitten napped on the mat",  # unlabeled, clearly cat-ish
    "stocks and bonds moved today",  # unlabeled, off-topic
]


class _Chunk:
    def __init__(self, chunk_id: str, text: str) -> None:
        self.chunk_id = chunk_id
        self.text = text
        self.metadata: dict[str, object] = {}


def _label_rows(texts: list[str], label: str, start: int) -> list[dict[str, object]]:
    return [
        {"chunk_id": f"c{start + i}", "label": label, "probability": 0.9, "created_at": None}
        for i in range(len(texts))
    ]


def _standard_corpus() -> tuple[list[dict[str, object]], list[_Chunk]]:
    labels = _label_rows(CAT_TEXTS, "cat", 1) + _label_rows(CAR_TEXTS, "car", 4)
    texts = CAT_TEXTS + CAR_TEXTS + EXTRA_TEXTS
    chunks = [_Chunk(f"c{i + 1}", text) for i, text in enumerate(texts)]
    return labels, chunks


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    tables: bool,
    labels: list[dict[str, object]],
    chunks: list[_Chunk],
) -> None:
    async def fake_present(pool: Any) -> bool:
        return tables

    async def fake_read(pool: Any, tenant_id: str, limit: int = 50) -> list[dict[str, object]]:
        return labels

    async def fake_chunks(pool: Any, tenant_id: str, limit: int = 100000) -> list[_Chunk]:
        return chunks

    monkeypatch.setattr(lexical_tools.writer, "labeling_tables_present", fake_present)
    monkeypatch.setattr(lexical_tools.writer, "read_labels", fake_read)
    monkeypatch.setattr(lexical_tools.engine, "read_chunks", fake_chunks)


async def test_absent_tables_answer_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, tables=False, labels=[], chunks=[])
    result = await lexical_tools.lexical_classify(None, tenant_id=TENANT)
    assert result["status"] == "unavailable"
    assert "migration 028" in str(result["reason"])
    assert result["predictions"] == []
    assert result["screened_count"] == 0


async def test_no_stored_labels_answer_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _labels, chunks = _standard_corpus()
    _patch(monkeypatch, tables=True, labels=[], chunks=chunks)
    result = await lexical_tools.lexical_classify(None, tenant_id=TENANT)
    assert result["status"] == "empty"
    assert "label" in str(result["reason"]).lower()
    assert "labeling-build" in str(result["remedy"])


async def test_single_class_answers_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    labels, chunks = _standard_corpus()
    labels = _label_rows(CAT_TEXTS, "cat", 1) + _label_rows(CAR_TEXTS[:1], "cat", 9)
    _patch(monkeypatch, tables=True, labels=labels, chunks=chunks)
    result = await lexical_tools.lexical_classify(None, tenant_id=TENANT)
    assert result["status"] == "empty"
    assert "two" in str(result["reason"])


async def test_thin_class_answers_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    labels, chunks = _standard_corpus()
    labels = _label_rows(CAT_TEXTS, "cat", 1) + _label_rows(CAR_TEXTS[:1], "car", 6)
    _patch(monkeypatch, tables=True, labels=labels, chunks=chunks)
    result = await lexical_tools.lexical_classify(None, tenant_id=TENANT)
    assert result["status"] == "empty"
    assert "2" in str(result["reason"])  # the per-class floor


async def test_unresolvable_label_ids_answer_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _labels, _chunks = _standard_corpus()
    labels = [
        {"chunk_id": "zzz-missing", "label": "cat", "probability": 0.9, "created_at": None},
        {"chunk_id": "yyy-missing", "label": "car", "probability": 0.9, "created_at": None},
    ]
    _patch(monkeypatch, tables=True, labels=labels, chunks=[_Chunk("c1", "text")])
    result = await lexical_tools.lexical_classify(None, tenant_id=TENANT)
    assert result["status"] == "empty"
    assert "stored chunk" in str(result["reason"])


async def test_success_envelope_is_sorted_and_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labels, chunks = _standard_corpus()
    _patch(monkeypatch, tables=True, labels=labels, chunks=chunks)
    result = await lexical_tools.lexical_classify(None, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["screened_count"] == len(chunks)
    rows = result["predictions"]
    assert [row["chunk_id"] for row in rows] == sorted(row["chunk_id"] for row in rows)
    assert len(rows) == len(chunks)
    for row in rows:
        assert 0.0 <= row["probability"] <= 1.0
        assert row["probability"] == round(row["probability"], 6)
    params = result["params"]
    assert params["classes"] == ["car", "cat"]  # sorted
    assert params["labeled_rows"] == 6
    assert params["model"] == "tfidf+logistic"


async def test_separable_corpus_predicts_the_obvious_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labels, chunks = _standard_corpus()
    _patch(monkeypatch, tables=True, labels=labels, chunks=chunks)
    result = await lexical_tools.lexical_classify(None, tenant_id=TENANT)
    by_id = {row["chunk_id"]: row for row in result["predictions"]}
    assert by_id["c7"]["label"] == "cat"  # "the kitten napped on the mat"
