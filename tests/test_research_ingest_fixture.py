"""Fixture E2E + G0/G5 gates + dynamic-ingest proof (todo-11 (d)/(e)).

Runs on the throwaway research DB (requires_postgres). Embedding goes through
a deterministic 1024-dim FakeEmbedder behind the embedding cache, so no
Ollama is needed; the live-model path is covered separately (requires_ollama).
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from research_db import TEST_TENANT

from corpus_kb.handlers.research_handler import get_research_handler
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.rag.embedder import FakeEmbedder

pytestmark = pytest.mark.requires_postgres

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research"
TENANT = UUID(TEST_TENANT)


class CountingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__({"embedding": {"dimensions": 1024}})
        self.calls = 0

    def embed_batch(self, texts):  # type: ignore[override]
        self.calls += 1
        return super().embed_batch(texts)


async def _wire(pool):
    from corpus_kb.domain.application import get_app
    from corpus_kb.handlers.research_handler import reset_research_handler

    reset_research_handler()
    app = get_app()
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    embedder = ResearchEmbedder(pool, CountingEmbedder(), model_revision="test-1024")
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    handler = get_research_handler(pool)
    return reader, projection, handler, embedder


async def _tenant_counts(pool: asyncpg.Pool, tenant: UUID, table: str) -> list:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(tenant))
        return await conn.fetch(f"SELECT * FROM {table} WHERE tenant_id = $1", str(tenant))


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    source = FIXTURES / "exchange_fixture_interview.md"
    target = tmp_path / "exchange_fixture_interview.md"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


async def test_fixture_e2e_event_stream_and_read_tables(research_pool, fixture_dir):
    reader, projection, handler, _ = await _wire(research_pool)
    baseline_dlq = {
        str(r["dlq_id"]) for r in await _tenant_counts(research_pool, TENANT, "projection_dlq")
    }
    labels = json.loads(
        (FIXTURES / "exchange_fixture_interview.labels.json").read_text(encoding="utf-8")
    )

    result = await handler.ingest_transcript(
        TENANT, str(fixture_dir / "exchange_fixture_interview.md")
    )
    assert result["status"] == "success"
    assert result["n_turns"] == labels["expected_turn_count"]
    assert result["parse_quality"] == "verified"

    await projection.catch_up(reader)

    speakers = await _tenant_counts(research_pool, TENANT, "research_speakers")
    by_label = {r["raw_label"]: r["role"] for r in speakers}
    for speaker, role in labels["role_expectations"].items():
        assert by_label.get(speaker) == role, f"role mismatch for {speaker}"
    assert all(r["role_confirmed"] is False for r in speakers)

    units = await _tenant_counts(research_pool, TENANT, "research_units")
    assert len(units) == labels["expected_turn_count"]
    assert all(r["text"] for r in units), "unit text not resolved from content store"
    moderator_units = [
        r for r in units if r["role_in_exchange"] in ("main_question", "probe", "transition")
    ]
    assert all(not r["is_codable"] for r in moderator_units)

    exchanges = await _tenant_counts(research_pool, TENANT, "research_exchanges")
    assert len(exchanges) == labels["expected_exchange_count"]
    for expected_method, row in zip(labels["exchange_link_methods"], exchanges, strict=False):
        assert row["link_method"] == expected_method

    # G5: stance + question-dependent routing (recorded via the event-only path)
    code = next(c for c in labels["codes"])
    handler.create_codebook_version(
        TENANT,
        label="v1-fixture",
        codes=[
            {
                "code_id": code["code_id"],
                "name": code["name"],
                "definition": code["definition"],
                "inclusion": "trust statements",
                "exclusion": "logistics complaints",
            }
        ],
    )
    run = handler.start_coding_run(TENANT, embed_model="fake-1024", model_revision="test")
    run_id = UUID(str(run["run_id"]))
    by_text = {r["text"]: r for r in units}

    def _unit_for(prefix: str):
        match = next((u for t, u in by_text.items() if t.startswith(prefix[:30])), None)
        assert match is not None, f"no unit starts with {prefix[:30]!r}"
        return match

    for unit_text in code["explicit_units"]:
        unit = _unit_for(unit_text)
        handler.record_assignment(
            TENANT,
            unit_id=int(unit["unit_id"]),
            code_id=uuid4(),
            run_id=run_id,
            evidence_basis="explicit_in_answer",
            stance="affirm",
            confidence="high",
        )
    for unit_text in code["question_dependent_units"]:
        unit = _unit_for(unit_text)
        handler.record_assignment(
            TENANT,
            unit_id=int(unit["unit_id"]),
            code_id=uuid4(),
            run_id=run_id,
            evidence_basis="question_dependent",
            stance="partial",
            confidence="high",
        )
    await projection.catch_up(reader)

    assignments = await _tenant_counts(research_pool, TENANT, "research_assignments")
    assert len(assignments) == 3
    qdep = [a for a in assignments if a["evidence_basis"] == "question_dependent"]
    assert qdep, "fixture missing question-dependent case"
    assert all(a["status"] == "review" for a in qdep), "question_dependent must route to review"
    assert all(a["confidence"] != "high" for a in qdep), "question_dependent confidence capped"
    explicit = [a for a in assignments if a["evidence_basis"] == "explicit_in_answer"]
    assert all(a["status"] == "auto" for a in explicit)

    # DLQ gains NO new rows from this fixture run (acceptance); rows injected
    # by other tests' induced-failure cases under the shared tenant are not
    # this run's business.
    dlq_rows = await _tenant_counts(research_pool, TENANT, "projection_dlq")
    new_dlq = [r for r in dlq_rows if str(r["dlq_id"]) not in baseline_dlq]
    assert new_dlq == [], f"DLQ gained rows during the fixture run: {new_dlq}"

    # embedding cache filled; units carry 1024-dim vectors
    cache = await _tenant_counts(research_pool, TENANT, "embedding_cache")
    assert cache, "embedding_cache empty after fixture ingest"
    embedded_units = [u for u in units if u["embedding"] is not None]
    assert embedded_units, "no unit vectors written"


async def test_rebuild_zero_embedder_calls_beyond_cache(research_pool, fixture_dir):
    reader, projection, handler, embedder = await _wire(research_pool)
    result = await handler.ingest_transcript(
        TENANT, str(fixture_dir / "exchange_fixture_interview.md")
    )
    doc_id = UUID(str(result["doc_id"]))
    await projection.catch_up(reader)
    calls_after_ingest = embedder._embedder.calls

    from research_db import superuser_conn

    conn = await superuser_conn()
    try:
        await conn.execute(
            "DELETE FROM projection_checkpoints WHERE projection_name='ResearchProjection'"
        )
        await conn.execute(
            "TRUNCATE research_reviews, research_signals, research_assignments, "
            "research_exchanges, research_units, research_speakers, research_projects "
            "RESTART IDENTITY"
        )
    finally:
        await conn.close()

    await projection.catch_up(reader)
    assert embedder._embedder.calls == calls_after_ingest, (
        "rebuild made embedder calls beyond embedding_cache misses"
    )
    async with research_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        after_units = await conn.fetch(
            "SELECT doc_id, seq, text, text_sha256, embedding "
            "FROM research_units WHERE tenant_id=$1 AND doc_id=$2 ORDER BY seq",
            TENANT,
            str(doc_id),
        )
        after_exchanges = await conn.fetch(
            "SELECT doc_id, seq, link_method, q_unit_ids, a_unit_ids "
            "FROM research_exchanges WHERE tenant_id=$1 AND doc_id=$2 ORDER BY seq",
            TENANT,
            str(doc_id),
        )
    assert after_units, "no units rebuilt for the ingest document"
    assert all(u["text"] for u in after_units), "content store did not survive rebuild drop-set"
    assert any(u["embedding"] is not None for u in after_units), "vectors not re-linked"
    assert after_exchanges, "exchanges not rebuilt"


async def test_dynamic_ingest_two_file_dir_edit_one(research_pool, tmp_path):
    reader, projection, handler, _ = await _wire(research_pool)
    file_a = tmp_path / "a.txt"
    file_a.write_text("Moderator: How is the weather?\nP1: Sunny, mostly.\n", encoding="utf-8")
    file_b = tmp_path / "b.txt"
    file_b.write_text("Moderator: Any final thoughts?\nP1: No, thanks.\n", encoding="utf-8")

    first = await handler.ingest_path(TENANT, str(tmp_path))
    assert first["ingested"] == 2 and first["skipped"] == 0

    second_unchanged = await handler.ingest_path(TENANT, str(tmp_path))
    assert second_unchanged["ingested"] == 0 and second_unchanged["skipped"] == 2

    file_b.write_text(
        "Moderator: Any final thoughts?\nP1: No, thanks. One more thing - pricing.\n",
        encoding="utf-8",
    )
    third = await handler.ingest_path(TENANT, str(tmp_path))
    assert third["ingested"] == 1, "editing ONE file must re-ingest exactly one"
    assert third["skipped"] == 1, "the unchanged file must file-hash no-op"

    await projection.catch_up(reader)
    turns = await _tenant_counts(research_pool, TENANT, "research_transcript_text")
    sunny_rows = [t for t in turns if t["text"] == "Sunny, mostly."]
    assert len(sunny_rows) == 1, "turn-text dedup violated for unchanged turns"


async def test_concurrent_assignment_dispatch_no_contention(research_pool):

    _, _, handler, _ = await _wire(research_pool)
    run = handler.start_coding_run(TENANT)
    run_id = UUID(str(run["run_id"]))
    import asyncio

    results = await asyncio.gather(
        *(
            asyncio.to_thread(
                handler.record_assignment,
                TENANT,
                unit_id=1000 + i,
                code_id=uuid4(),
                run_id=run_id,
                evidence_basis="explicit_in_answer",
            )
            for i in range(4)
        )
    )
    assert all(r["status"] == "success" for r in results)
