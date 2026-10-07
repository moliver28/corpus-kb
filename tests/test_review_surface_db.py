"""Review accept/override execution surface (todo 16, r7 named surface).

requires_postgres: runs the real event -> projector path on the throwaway
research DB. `corpus-kb review accept|override` delegates to
review_surface.execute_review; this test drives the module directly.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from research_db import TEST_TENANT

from corpus_kb.handlers.research_handler import get_research_handler
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.rag.embedder import FakeEmbedder
from corpus_kb.research.review_surface import DECISIONS, execute_review

pytestmark = pytest.mark.requires_postgres

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research"
TENANT = UUID(TEST_TENANT)


async def _wire(pool):
    from corpus_kb.domain.application import get_app
    from corpus_kb.handlers.research_handler import reset_research_handler

    reset_research_handler()
    app = get_app()
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    embedder = ResearchEmbedder(pool, FakeEmbedder({"embedding": {"dimensions": 1024}}))
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    handler = get_research_handler(pool)
    return reader, projection, handler


async def _rows(pool, table: str) -> list:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        return await conn.fetch(f"SELECT * FROM {table} WHERE tenant_id = $1", str(TENANT))


async def _seed_projected_assignment(pool, tmp_path):
    """One auto-accepted assignment event, projected into research_assignments.

    Event-clean seeding: ingest the fixture transcript through the real
    handler (raw-SQL documents are not event-derived and break the
    replay-rebuild proof), pick a codable unit, record the assignment.
    """
    reader, projection, handler = await _wire(pool)
    source = FIXTURES / "exchange_fixture_interview.md"
    target = tmp_path / "exchange_fixture_interview.md"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    result = await handler.ingest_transcript(TENANT, str(target), project_id=uuid4())
    assert result["status"] == "success"
    await projection.catch_up(reader)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        unit_row = await conn.fetchrow(
            "SELECT unit_id FROM research_units WHERE tenant_id = $1 AND is_codable "
            "ORDER BY unit_id LIMIT 1",
            str(TENANT),
        )
    assert unit_row is not None, "fixture ingest produced no codable unit"
    run = handler.start_coding_run(TENANT, embed_model="fake-1024")
    run_id = UUID(str(run["run_id"]))
    recorded = handler.record_assignment(
        TENANT,
        unit_id=int(unit_row["unit_id"]),
        code_id=uuid4(),
        run_id=run_id,
        evidence_basis="explicit_in_answer",
        stance="affirm",
        confidence="high",
    )
    aggregate_id = UUID(str(recorded["assignment_id"]))
    await projection.catch_up(reader)
    return aggregate_id


async def _assignment_by_aggregate(pool, aggregate_id: UUID):
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        return await conn.fetchrow(
            "SELECT * FROM research_assignments "
            "WHERE tenant_id = $1 AND assignment_aggregate_id = $2",
            str(TENANT),
            str(aggregate_id),
        )


async def test_review_accept_then_override_lands_and_flips_status(
    research_pool, research_dsn, tmp_path
):
    aggregate_id = await _seed_projected_assignment(research_pool, tmp_path)

    accepted = await execute_review(
        research_pool, TENANT, research_dsn, aggregate_id, "alice", "accept", ""
    )
    assert accepted["status"] == "success"
    assert accepted["decision"] == "accept"
    assert accepted["projection"]["reviewed_events_applied"] >= 1

    reviews = await _rows(research_pool, "research_reviews")
    mine_id = (await _assignment_by_aggregate(research_pool, aggregate_id))["assignment_id"]
    mine_reviews = [r for r in reviews if r["assignment_id"] == mine_id]
    assert len(mine_reviews) == 1
    assert mine_reviews[0]["reviewer"] == "alice"
    assert mine_reviews[0]["decision"] == "confirmed"
    mine = await _assignment_by_aggregate(research_pool, aggregate_id)
    assert mine["status"] == "confirmed"

    overridden = await execute_review(
        research_pool,
        TENANT,
        research_dsn,
        aggregate_id,
        "bob",
        "override",
        "link corrected: evidence sits in the prior exchange",
    )
    assert overridden["decision"] == "override"
    reviews = await _rows(research_pool, "research_reviews")
    mine_reviews = [r for r in reviews if r["assignment_id"] == mine_id]
    assert len(mine_reviews) == 2
    by_reviewer = {r["reviewer"]: r for r in mine_reviews}
    assert by_reviewer["bob"]["decision"] == "overridden"
    assert "link corrected" in str(by_reviewer["bob"]["note"])
    mine = await _assignment_by_aggregate(research_pool, aggregate_id)
    assert mine["status"] == "overridden"


async def test_review_rejects_unknown_decision(research_pool, research_dsn, tmp_path):
    aggregate_id = await _seed_projected_assignment(research_pool, tmp_path)
    with pytest.raises(ValueError):
        await execute_review(
            research_pool, TENANT, research_dsn, aggregate_id, "carol", "maybe", ""
        )


def test_decisions_are_the_named_pair():
    assert DECISIONS == ("accept", "override")
