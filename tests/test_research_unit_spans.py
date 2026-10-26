"""Research-unit char spans, end to end (unified-ingestion Wave 3).

The canonical transcript text is the normalized turn texts joined by a
blank line; every unit's (start_char, end_char) must slice exactly to its
text. Covered on both engines: Postgres through the full handler + fixture
ingest, DuckDB through the projection harness with span-carrying payloads.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from research_db import TEST_TENANT

from corpus_kb.handlers.research_handler import get_research_handler, reset_research_handler
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.rag.embedder import FakeEmbedder

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research"
TENANT = UUID(TEST_TENANT)

_CANONICAL_JOIN = "\n\n"


def _canonical(units: list[Any]) -> str:
    return _CANONICAL_JOIN.join(str(u["text"]) for u in units)


async def _wire(pool: Any):
    from corpus_kb.domain.application import get_app

    reset_research_handler()
    app = get_app()
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    embedder = ResearchEmbedder(
        pool, FakeEmbedder({"embedding": {"dimensions": 1024}}), model_revision="test-1024"
    )
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    handler = get_research_handler(pool)
    return reader, projection, handler


async def _doc_units(pool: Any, doc_id: str) -> list[Any]:
    """One document's units, ascending by seq, under the test tenant GUC."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        return list(
            await conn.fetch(
                "SELECT seq, text, text_sha256, start_char, end_char "
                "FROM research_units WHERE tenant_id = $1 AND doc_id = $2 ORDER BY seq",
                TENANT,
                doc_id,
            )
        )


async def _drop_read_models(pool: Any) -> None:
    """Reset the research read models and the projection checkpoint.

    The checkpoint row lives under DEFAULT_TENANT (catch_up reads it under
    that tenant), so the tenant GUC for the DELETE must name DEFAULT_TENANT;
    the read-model TRUNCATE is owner DDL and runs in the same transaction.
    """
    from corpus_kb.projections.research._common import DEFAULT_TENANT

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)", str(DEFAULT_TENANT)
        )
        await conn.execute(
            "DELETE FROM projection_checkpoints WHERE projection_name='ResearchProjection'"
        )
        await conn.execute(
            "TRUNCATE research_reviews, research_signals, research_assignments, "
            "research_exchanges, research_units, research_speakers, research_projects "
            "RESTART IDENTITY"
        )


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    source = FIXTURES / "exchange_fixture_interview.md"
    target = tmp_path / "exchange_fixture_interview.md"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


@pytest.mark.requires_postgres
async def test_fixture_units_carry_canonical_spans(research_pool, fixture_dir):
    reader, projection, handler = await _wire(research_pool)
    result = await handler.ingest_transcript(
        TENANT, str(fixture_dir / "exchange_fixture_interview.md")
    )
    assert result["status"] == "success"
    await projection.catch_up(reader)

    units = await _doc_units(research_pool, result["doc_id"])
    assert len(units) == result["n_turns"]
    canonical = _canonical(units)
    for unit in units:
        assert unit["start_char"] is not None, f"seq {unit['seq']} has no span"
        assert unit["end_char"] is not None, f"seq {unit['seq']} has no span"
        assert canonical[unit["start_char"] : unit["end_char"]] == unit["text"]
    # Deterministic layout: first turn at 0, separator between turns.
    assert units[0]["start_char"] == 0
    for prev, curr in pairwise(units):
        assert curr["start_char"] == prev["end_char"] + len(_CANONICAL_JOIN)


@pytest.mark.requires_postgres
async def test_spans_survive_a_rebuild_from_events(research_pool, fixture_dir):
    reader, projection, handler = await _wire(research_pool)
    result = await handler.ingest_transcript(
        TENANT, str(fixture_dir / "exchange_fixture_interview.md")
    )
    await projection.catch_up(reader)
    before = await _doc_units(research_pool, result["doc_id"])

    await _drop_read_models(research_pool)

    await projection.catch_up(reader)
    after = await _doc_units(research_pool, result["doc_id"])
    assert [(u["seq"], u["start_char"], u["end_char"]) for u in after] == [
        (u["seq"], u["start_char"], u["end_char"]) for u in before
    ]
    canonical = _canonical(after)
    for unit in after:
        assert canonical[unit["start_char"] : unit["end_char"]] == unit["text"]
    assert result["status"] == "success"


async def test_duckdb_units_carry_spans(tmp_path: Path) -> None:
    import hashlib

    from test_research_projection_duckdb import (
        _app,
        _catch_up,
        _migrated_gateway,
        _new_document,
        _turn,
    )

    from corpus_kb.projections.research._common import store_turn_texts

    def sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    gateway = await _migrated_gateway(tmp_path, "research-spans.duckdb")
    app = _app(tmp_path)
    try:
        doc = _new_document(TENANT, "sym://spans")
        app.save(doc)
        q_text = "What did the panel conclude?"
        a_text = "The panel concluded the target is reachable."
        await store_turn_texts(gateway, TENANT, {sha(q_text): q_text, sha(a_text): a_text})
        saved = app.repository.get(UUID(str(doc.id)))
        saved.add_turn_batch(
            TENANT,
            [
                {
                    **_turn(q_text, 0, "main_question", "moderator"),
                    "start_char": 0,
                    "end_char": len(q_text),
                },
                {
                    **_turn(a_text, 1, "answer", "participant"),
                    "start_char": len(q_text) + 2,
                    "end_char": len(q_text) + 2 + len(a_text),
                },
            ],
        )
        app.save(saved)

        await _catch_up(gateway, app)
        rows = await gateway.fetch(
            "SELECT seq, text, start_char, end_char FROM research_units ORDER BY seq"
        )
        assert [int(r["start_char"]) for r in rows] == [0, len(q_text) + 2]
        canonical = "\n\n".join(str(r["text"]) for r in rows)
        for row in rows:
            assert canonical[row["start_char"] : row["end_char"]] == row["text"]
        assert [int(r["seq"]) for r in rows] == [0, 1]
    finally:
        await gateway.close()
