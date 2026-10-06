"""STEP 0 ES-foundation proofs as pytest (todo-11 spike, requires_postgres).

Ground truth proven by the spike on the live local PostgreSQL 16 + pgvector
0.8.3 (docker absent — declared substitution, todo-5 precedent):
  * eventsourcing[postgres] works; the lib owns an app-namespaced events
    table (CorpusApplication -> corpusapplication_events) shaped
    (originator_id, originator_version, topic, state, notification_id).
  * the notification_id read path decodes via the app Mapper.
  * DocumentsProjection writes REAL deterministic chunk ids (not UUID(int=0))
    and EmbedChunksProjection derives the same ids from chunk_texts.
  * CheckpointManager persists last_sequence under FORCE RLS (one txn).
  * snapshot-bearing aggregates replay identically; halfvec ops exist.
  * replay->rebuild is byte-identical; failures land in the DLQ.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest
from research_db import TEST_TENANT, superuser_conn

from corpus_kb.domain.aggregates import Document
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.documents_projection import DocumentsProjection
from corpus_kb.projections.embed_projection import EmbedChunksProjection
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.ids import deterministic_chunk_id, deterministic_event_id
from corpus_kb.rag.embedder import FakeEmbedder

pytestmark = pytest.mark.requires_postgres

TENANT = UUID(TEST_TENANT)


async def _reader(pool: asyncpg.Pool):
    from corpus_kb.domain.application import get_app

    app = get_app()
    return EventReader(pool, app.mapper, app.recorder.events_table_name), app


def _fake4096() -> FakeEmbedder:
    return FakeEmbedder({"embedding": {"dimensions": 4096}})


async def _projections(pool: asyncpg.Pool, reader_and_app):
    reader, app = reader_and_app
    checkpoint = CheckpointManager(pool)
    dlq = DLQHandler(pool)
    docs = DocumentsProjection(pool, checkpoint, dlq)
    embeds = EmbedChunksProjection(pool, _fake4096(), checkpoint, dlq)
    return reader, app, docs, embeds


async def _save_two_documents() -> tuple[UUID, UUID]:
    from corpus_kb.domain.application import get_app

    doc = Document(
        tenant_id=TENANT,
        source=f"spike://{uuid.uuid4()}",
        source_type="transcript",
        file_hash="c" * 64,
    )
    doc.add_chunks(chunk_count=2, chunk_texts=["Moderator: How?", "P: Fine, mostly."])
    doc2 = Document(
        tenant_id=TENANT,
        source=f"spike://{uuid.uuid4()}",
        source_type="transcript",
        file_hash="d" * 64,
    )
    doc2.add_chunks(chunk_count=1, chunk_texts=["P2: Same experience here."])
    app = get_app()
    app.save(doc)
    app.save(doc2)
    return UUID(str(doc.id)), UUID(str(doc2.id))


async def test_lib_schema_is_topic_state_notification_id(superuser):
    conn = superuser
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE '%events'"
    )
    assert tables, "eventsourcing events table missing"
    cols = [
        r["column_name"]
        for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=$1 ORDER BY ordinal_position",
            tables[0]["tablename"],
        )
    ]
    assert cols == [
        "originator_id",
        "originator_version",
        "topic",
        "state",
        "notification_id",
    ]


async def test_read_path_decodes_via_mapper(pool, reader_and_app):
    reader, _ = reader_and_app
    doc_id, _doc2 = await _save_two_documents()
    notifications = await reader.read_since(0, limit=100)
    topics = [n.topic for n in notifications]
    assert any(
        n.topic.endswith("Document.Ingested") and str(n.originator_id) == str(doc_id)
        for n in notifications
    )
    assert all(":" in t for t in topics)
    ingested = next(n for n in notifications if n.event_type == "Document.Ingested")
    assert UUID(str(ingested.event.tenant_id)) == TENANT
    added = next(n for n in notifications if n.event_type == "Document.ChunksAdded")
    assert added.event.chunk_texts[0].startswith("Moderator:")


async def test_real_path_projects_rows_and_vectors(pool, reader_and_app):
    reader, _, docs, embeds = await _projections(pool, reader_and_app)
    doc_id, _ = await _save_two_documents()
    await docs.catch_up(reader, TENANT)
    await embeds.catch_up(reader, TENANT)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        row = await conn.fetchrow("SELECT * FROM documents WHERE doc_id = $1", str(doc_id))
        assert row is not None and row["source_type"] == "transcript"
        chunks = await conn.fetch(
            "SELECT chunk_id, chunk_index, text FROM chunks WHERE doc_id = $1 ORDER BY chunk_index",
            str(doc_id),
        )
        assert len(chunks) == 2
        assert all(r["chunk_id"] != UUID(int=0) for r in chunks)
        assert [r["chunk_id"] for r in chunks] == [
            deterministic_chunk_id(doc_id, 0),
            deterministic_chunk_id(doc_id, 1),
        ]
        vectors = await conn.fetch(
            "SELECT chunk_id FROM chunks_vectors WHERE tenant_id = $1", str(TENANT)
        )
        assert len(vectors) >= 2


async def test_checkpoint_persists_under_force_rls(pool, reader_and_app):
    reader, _, docs, _ = await _projections(pool, reader_and_app)
    await _save_two_documents()
    await docs.catch_up(reader, TENANT)
    checkpoint = CheckpointManager(pool)
    cp = await checkpoint.get_checkpoint("DocumentsProjection", TENANT)
    assert cp is not None and (cp["last_sequence"] or 0) > 0
    conn = await superuser_conn()
    try:
        row = await conn.fetchrow(
            "SELECT last_sequence FROM projection_checkpoints "
            "WHERE projection_name='DocumentsProjection' AND tenant_id=$1",
            TENANT,
        )
        assert row is not None and row["last_sequence"] == cp["last_sequence"]
    finally:
        await conn.close()


async def test_same_timestamp_events_never_skipped(pool, reader_and_app):
    reader, _, docs, embeds = await _projections(pool, reader_and_app)
    await _save_two_documents()
    await docs.catch_up(reader, TENANT)
    await embeds.catch_up(reader, TENANT)
    cp = await CheckpointManager(pool).get_checkpoint("DocumentsProjection", TENANT)
    table = await _events_table_name()
    conn = await superuser_conn()
    try:
        high = await conn.fetchval(f"SELECT MAX(notification_id) FROM {table}")
    finally:
        await conn.close()
    assert cp["last_sequence"] == high
    assert high >= 2


async def _events_table_name() -> str:
    conn = await superuser_conn()
    try:
        row = await conn.fetchrow(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE '%events'"
        )
        return row["tablename"]
    finally:
        await conn.close()


async def test_replay_rebuild_byte_identical(pool, reader_and_app):
    reader, _, docs, embeds = await _projections(pool, reader_and_app)
    await _save_two_documents()
    await docs.catch_up(reader, TENANT)
    await embeds.catch_up(reader, TENANT)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        before_docs = await conn.fetch(
            "SELECT doc_id, source, source_type FROM documents WHERE tenant_id=$1 ORDER BY doc_id",
            TENANT,
        )
        before_chunks = await conn.fetch(
            "SELECT chunk_id, doc_id, chunk_index, text FROM chunks "
            "WHERE tenant_id=$1 ORDER BY chunk_id",
            TENANT,
        )

    checkpoint = CheckpointManager(pool)
    await checkpoint.update_checkpoint(
        "DocumentsProjection",
        TENANT,
        deterministic_event_id(uuid.uuid4(), 1),
        datetime.now(tz=UTC),
        0,
    )
    await checkpoint.update_checkpoint(
        "EmbedChunksProjection",
        TENANT,
        deterministic_event_id(uuid.uuid4(), 1),
        datetime.now(tz=UTC),
        0,
    )
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        await conn.execute("DELETE FROM chunks_vectors WHERE tenant_id=$1", TENANT)
        await conn.execute("DELETE FROM chunks WHERE tenant_id=$1", TENANT)
        await conn.execute("DELETE FROM documents WHERE tenant_id=$1", TENANT)
    await docs.catch_up(reader, TENANT)
    await embeds.catch_up(reader, TENANT)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        after_docs = await conn.fetch(
            "SELECT doc_id, source, source_type FROM documents WHERE tenant_id=$1 ORDER BY doc_id",
            TENANT,
        )
        after_chunks = await conn.fetch(
            "SELECT chunk_id, doc_id, chunk_index, text FROM chunks "
            "WHERE tenant_id=$1 ORDER BY chunk_id",
            TENANT,
        )
    assert [tuple(r) for r in before_docs] == [tuple(r) for r in after_docs]
    assert [tuple(r) for r in before_chunks] == [tuple(r) for r in after_chunks]


async def test_projection_failure_lands_dlq_row(pool, reader_and_app):
    _, _, docs, _ = await _projections(pool, reader_and_app)
    dlq = DLQHandler(pool)
    bad_event_id = deterministic_event_id(uuid.uuid4(), 1)
    await docs.process_event(
        TENANT,
        bad_event_id,
        "Ingested",
        {
            "aggregate_id": str(uuid.uuid4()),
            "source": "spike://bad",
            "source_type": "text",
            "metadata": {"unserializable": object()},
        },
        datetime.now(tz=UTC),
    )
    failures = await dlq.list_failures("DocumentsProjection", TENANT)
    assert any(str(f["event_id"]) == str(bad_event_id) for f in failures)


async def test_rls_blocks_cross_tenant_reads(pool):
    other = uuid.uuid4()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        await conn.execute(
            "INSERT INTO research_transcript_text (tenant_id, text_sha256, text) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            str(TENANT),
            uuid.uuid4().hex,
            "tenant A secret",
        )
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(other))
        seen = await conn.fetchval("SELECT count(*) FROM research_transcript_text")
    assert seen == 0


async def test_snapshot_replay_identical(pool, reader_and_app):
    _, app, _, _ = await _projections(pool, reader_and_app)
    doc_id, _ = await _save_two_documents()
    rebuilt = app.repository.get(doc_id)
    assert rebuilt.chunk_count == 2
    assert rebuilt.source_type == "transcript"
    conn = await superuser_conn()
    try:
        table = await conn.fetchval(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename LIKE '%snapshots'"
        )
        assert table, "snapshots table missing"
        snaps = await conn.fetchval(f"SELECT count(*) FROM {table}")
        assert snaps >= 1, "auto-snapshot interval produced no snapshot"
    finally:
        await conn.close()


async def test_halfvec_supported(superuser):
    distance = await superuser.fetchval("SELECT '[1,2,3]'::halfvec <=> '[4,5,6]'::halfvec")
    assert distance is not None
    version = await superuser.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")
    major = int(version.split(".")[1])
    assert major >= 7, f"pgvector {version} lacks halfvec (>=0.7 required)"


async def test_tenant_contract_on_all_document_events(pool, reader_and_app):
    reader, _, _, _ = await _projections(pool, reader_and_app)
    doc = Document(tenant_id=TENANT, source=f"spike://{uuid.uuid4()}", source_type="transcript")
    doc.add_turn_batch(
        tenant_id=TENANT,
        turns=[{"seq": 0, "text_sha256": uuid.uuid4().hex, "speaker": "M", "role": "moderator"}],
    )
    from corpus_kb.domain.application import get_app

    get_app().save(doc)
    notifications = await reader.read_since(0, limit=1000)
    research_topics = {
        "Document.Ingested",
        "Document.TurnsParsed",
        "Document.ExchangesLinked",
        "CodebookVersion.Created",
        "CodebookVersion.CodeAdded",
        "CodingRun.Started",
        "CodingAssignment.Recorded",
    }
    for n in notifications:
        if n.event_type in research_topics:
            assert getattr(n.event, "tenant_id", None) is not None, (
                f"TENANT CONTRACT violated: {n.topic} carries no tenant_id"
            )


async def test_legacy_events_do_not_dlq(pool, research_pool, reader_and_app):
    """M4 regression: the research projection must SKIP legacy tenant-less
    events (ChunksAdded from the frozen direct-write path), never DLQ them."""
    from uuid import uuid4

    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.rag.embedder import FakeEmbedder

    _, app, _, _ = await _projections(pool, reader_and_app)
    doc = Document(tenant_id=TENANT, source=f"spike://{uuid4()}")
    doc.add_chunks(chunk_count=1, chunk_texts=["legacy chunk text"])
    app.save(doc)

    dlq = DLQHandler(research_pool)
    before = {str(r["dlq_id"]) for r in await dlq.list_failures("ResearchProjection", TENANT)}

    embedder = ResearchEmbedder(research_pool, FakeEmbedder({"embedding": {"dimensions": 1024}}))
    projection = ResearchProjection(research_pool, CheckpointManager(research_pool), dlq, embedder)
    await projection.catch_up(reader_and_app[0])

    after = await dlq.list_failures("ResearchProjection", TENANT)
    new_rows = [r for r in after if str(r["dlq_id"]) not in before]
    assert new_rows == [], f"legacy events produced DLQ rows: {new_rows}"


async def test_migration_016_rollback_and_reapply(research_pool, research_dsn):
    """rollback_016 drops the research read models + last_sequence and clears
    the research checkpoint; re-applying 016 restores the tables. Must stay
    LAST in this file (it drops tables other tests use)."""
    from pathlib import Path

    migrations = Path(__file__).resolve().parent.parent / "src" / "corpus_kb" / "migrations"

    conn = await superuser_conn()
    try:
        await conn.execute(
            (migrations / "rollback_016_research_domain.sql").read_text(encoding="utf-8")
        )
        left = await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE tablename='research_units'"
        )
        assert left == 0, "rollback_016 did not drop research_units"
        seq_gone = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='projection_checkpoints' AND column_name='last_sequence'"
        )
        assert seq_gone == 0, "rollback_016 did not drop last_sequence"
    finally:
        await conn.close()

    # Re-apply as corpus_user (the migration runner's role): tables must be
    # owned by corpus_user or the projections lose access.
    conn = await asyncpg.connect(research_dsn)
    try:
        await conn.execute((migrations / "016_research_domain.sql").read_text(encoding="utf-8"))
    finally:
        await conn.close()

    conn = await superuser_conn()
    try:
        back = await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE tablename LIKE 'research_%'"
        )
        assert back >= 9, "016 re-apply did not restore research tables"
        unguarded = await conn.fetch(
            "SELECT relname FROM pg_class WHERE relname LIKE 'research_%' "
            "AND relkind='r' AND NOT relrowsecurity"
        )
        assert unguarded == [], (
            f"re-apply left research tables WITHOUT row-level security: {[r[0] for r in unguarded]}"
        )
    finally:
        await conn.close()
