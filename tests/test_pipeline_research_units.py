"""Research front-end canonical document + UnitSink port tests (Wave 3).

The offset invariant is the contract: the canonical transcript text slices
exactly to each turn's text. The UnitSink delegates VERBATIM to the research
seams and reports honest zeros on the corpus legs it must not write.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from corpus_kb.domain.transcript import TurnSpec
from corpus_kb.research.conn import Store
from corpus_kb.tools.pipeline.research_units import research_document
from corpus_kb.tools.pipeline.sinks import UnitSink

TENANT = UUID("00000000-0000-0000-0000-000000000002")


def _turns() -> list[TurnSpec]:
    return [
        TurnSpec(seq=0, speaker="Moderator", text="Welcome.  Let's start."),
        TurnSpec(seq=1, speaker="P1", text="Glad to be here."),
    ]


def _sink() -> UnitSink:
    return UnitSink(cast("Store", object()), TENANT)


def test_research_document_offset_invariant_holds() -> None:
    doc = research_document("interview.md", "txt", _turns())
    assert doc.offset_invariant_problems() == []
    for chunk in doc.chunks:
        span = doc.chunk_span(chunk)
        assert span is not None
        assert doc.text[span.start_char : span.end_char] == chunk.text


def test_research_document_stamps_spans_on_turns() -> None:
    turns = _turns()
    doc = research_document("interview.md", "txt", turns)
    first_text = "Welcome. Let's start."  # normalized: whitespace collapsed
    assert turns[0].start_char == 0
    assert turns[0].end_char == len(first_text)
    assert turns[1].start_char == len(first_text) + 2  # the "\n\n" separator
    payload = turns[1].to_payload()
    assert payload["start_char"] == turns[1].start_char
    assert payload["end_char"] == turns[1].end_char
    assert doc.chunks[0].metadata["seq"] == 0
    assert doc.chunks[0].metadata["speaker"] == "Moderator"
    from corpus_kb.domain.transcript import text_sha256

    assert str(doc.chunks[0].chunk_id) == text_sha256(first_text)


def test_research_document_is_deterministic() -> None:
    one = research_document("interview.md", "txt", _turns())
    two = research_document("interview.md", "txt", _turns())
    assert one.text == two.text
    assert one.file_hash == two.file_hash
    assert [str(c.chunk_id) for c in one.chunks] == [str(c.chunk_id) for c in two.chunks]
    assert [(c.source_start_char, c.source_end_char) for c in one.chunks] == [
        (c.source_start_char, c.source_end_char) for c in two.chunks
    ]


def test_turn_payload_omits_spans_when_unset() -> None:
    payload: dict[str, Any] = TurnSpec(seq=0, speaker="A", text="hi").to_payload()
    assert "start_char" not in payload
    assert "end_char" not in payload


def test_unit_sink_carries_every_ingest_sink_member() -> None:
    sink = _sink()
    for name in (
        "tenant_id",
        "gateway",
        "unchanged_doc_id",
        "begin_run",
        "finish_run",
        "fail_run",
        "update_document",
        "store_chunks",
        "store_vectors",
        "store_entities",
        "store_relations",
    ):
        assert hasattr(sink, name), name


async def test_store_units_orders_by_seq_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.projections.research import _common

    calls: list[tuple[str, int]] = []

    async def fake_speaker(conn: Any, tenant_id: UUID, doc_id: UUID, turn: dict[str, Any]) -> None:
        calls.append(("speaker", int(turn["seq"])))

    async def fake_unit(
        conn: Any,
        tenant_id: UUID,
        doc_id: UUID,
        project_id: str | None,
        turn: dict[str, Any],
        texts: dict[str, str],
    ) -> None:
        calls.append(("unit", int(turn["seq"])))

    monkeypatch.setattr(_common, "upsert_speaker", fake_speaker)
    monkeypatch.setattr(_common, "upsert_unit", fake_unit)

    turns = [
        {"seq": 2, "text_sha256": "b", "speaker": "P1"},
        {"seq": 0, "text_sha256": "a", "speaker": "Moderator"},
        {"seq": 1, "text_sha256": "missing", "speaker": "P2"},
    ]
    written = await _sink().store_units(None, uuid4(), None, turns, {"a": "A", "b": "B"})
    assert written == 2
    # Seq order, speaker before unit, unknown sha skips only the unit write.
    assert calls == [
        ("speaker", 0),
        ("unit", 0),
        ("speaker", 1),
        ("speaker", 2),
        ("unit", 2),
    ]


async def test_upsert_unit_binds_optional_spans() -> None:
    from corpus_kb.projections.research import _common

    class RecordingConn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        async def execute(self, sql: str, *args: Any) -> str:
            self.calls.append((sql, args))
            return "INSERT 0 1"

    conn = RecordingConn()
    turn = {
        "seq": 3,
        "text_sha256": "a" * 64,
        "speaker": "P1",
        "start_char": 5,
        "end_char": 9,
    }
    await _common.upsert_unit(conn, TENANT, uuid4(), None, turn, {turn["text_sha256"]: "text"})
    sql, args = conn.calls[0]
    assert "start_char, end_char" in sql
    assert args[12] == 5
    assert args[13] == 9


async def test_upsert_unit_binds_null_spans_for_legacy_payloads() -> None:
    from corpus_kb.projections.research import _common

    class RecordingConn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        async def execute(self, sql: str, *args: Any) -> str:
            self.calls.append((sql, args))
            return "INSERT 0 1"

    conn = RecordingConn()
    turn = {"seq": 1, "text_sha256": "b" * 64, "speaker": "P1"}
    await _common.upsert_unit(conn, TENANT, uuid4(), None, turn, {turn["text_sha256"]: "text"})
    _, args = conn.calls[0]
    assert args[12] is None
    assert args[13] is None


async def test_ledger_legs_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.research import dynamic_ingest

    seen: dict[str, Any] = {}
    doc_id = uuid4()

    async def fake_doc_id(pool: Any, tenant_id: UUID, path: Any, sha: str) -> UUID | None:
        seen["probe"] = (tenant_id, str(path), sha)
        return doc_id

    async def fake_record(
        pool: Any, tenant_id: UUID, path: Any, sha: str, source_type: str, recorded: UUID | None
    ) -> None:
        seen["record"] = (tenant_id, str(path), sha, source_type, recorded)

    monkeypatch.setattr(dynamic_ingest, "ledger_doc_id", fake_doc_id)
    monkeypatch.setattr(dynamic_ingest, "ledger_record", fake_record)

    sink = _sink()
    assert await sink.unchanged_doc_id("interview.md", "f" * 64) == str(doc_id)
    assert seen["probe"] == (TENANT, "interview.md", "f" * 64)

    await sink.record_ledger("interview.md", "f" * 64, "interview", doc_id)
    assert seen["record"] == (TENANT, "interview.md", "f" * 64, "interview", doc_id)


async def test_corpus_legs_report_honest_zeros() -> None:
    sink = _sink()
    assert await sink.store_chunks([]) == set()
    assert await sink.store_vectors([], "model") == 0
    assert await sink.store_entities([]) == {}
    assert await sink.store_relations([]) == 0

    class FakeDocument:
        document_id = "doc-1"

    run_id, file_hash = await sink.begin_run(cast("Any", FakeDocument()), "a" * 64)
    assert run_id == "doc-1"  # identity mapping; the events are the ledger
    assert file_hash == "a" * 64
    await sink.finish_run("run-1", "a" * 64, {}, [])
    await sink.fail_run("run-1", "boom")
    await sink.update_document(cast("Any", FakeDocument()), "a" * 64)


def test_unit_sink_tenant_id_is_text() -> None:
    assert _sink().tenant_id == str(TENANT)
