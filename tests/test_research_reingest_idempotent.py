"""Re-ingest idempotency (issue #529): no DLQ when research re-ingests.

Corpus-ingesting a source keys the documents row under one doc_id;
research-ingesting the SAME source aggregates it under a NEW id. Before
the fix, the upsert kept the old id, the speaker and unit writes broke
the foreign key to documents, and the events landed in the DLQ. These
tests run the corpus-then-research sequence on a migrated DuckDB file,
and on the throwaway research Postgres database when reachable.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.domain.aggregates import Document
from corpus_kb.domain.application import CorpusApplication, get_app
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.research._common import store_turn_texts
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.rag.embedder import FakeEmbedder
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

Q_TEXT = "What did the panel conclude about the target?"
A_TEXT = "The panel concluded the target is achievable."

_PG_TENANT_SQL = "SELECT set_config('app.current_tenant_id', $1, true)"
_DLQ_SQL = "SELECT count(*) AS n FROM projection_dlq WHERE tenant_id = $1::uuid"
_UNITS_SQL = (
    "SELECT count(*) AS n FROM research_units WHERE tenant_id = $1::uuid AND doc_id = $2::uuid"
)
_DOCS_SQL = "SELECT doc_id FROM documents WHERE tenant_id = $1::uuid AND source = $2"
_SEED_DOC_SQL = (
    "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
    " VALUES ($1::uuid, $2::uuid, $3, 'transcript')"
)
_SEED_CHUNK_SQL = (
    "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)"
    " VALUES ($1::uuid, $2::uuid, $3::uuid, 0, 'corpus ingest chunk')"
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _turn(text: str, seq: int, role_in_exchange: str, speaker: str) -> dict[str, object]:
    return {
        "seq": seq,
        "text_sha256": _sha(text),
        "speaker": speaker,
        "role": speaker,
        "role_in_exchange": role_in_exchange,
    }


def _turns() -> list[dict[str, object]]:
    return [
        _turn(Q_TEXT, 1, "main_question", "moderator"),
        _turn(A_TEXT, 2, "answer", "participant"),
    ]


def _exchanges() -> list[dict[str, object]]:
    return [{"seq": 1, "q_unit_seqs": [1], "a_unit_seqs": [2], "link_method": "adjacency"}]


async def _seed_corpus(conn: Any, tenant: UUID, source: str, duck: bool) -> str:
    """The corpus-ingest write: one documents row plus one chunk."""
    corpus_doc = str(uuid4())
    statements = (
        (_SEED_DOC_SQL, (corpus_doc, str(tenant), source)),
        (_SEED_CHUNK_SQL, (str(uuid4()), str(tenant), corpus_doc)),
    )
    if duck:
        for sql, args in statements:
            await conn.execute(sql, *args)
        return corpus_doc
    async with conn.acquire() as c, c.transaction():
        await c.execute(_PG_TENANT_SQL, str(tenant))
        for sql, args in statements:
            await c.execute(sql, *args)
    return corpus_doc


async def _research_ingest(conn: Any, app: Any, tenant: UUID, source: str) -> UUID:
    """Emit one research aggregate for `source` (Ingested, turns, links)."""
    doc = Document(
        tenant_id=tenant, source=source, source_type="transcript", file_hash=uuid.uuid4().hex
    )
    app.save(doc)
    doc_id = UUID(str(doc.id))
    await store_turn_texts(conn, tenant, {_sha(A_TEXT): A_TEXT, _sha(Q_TEXT): Q_TEXT})
    saved = app.repository.get(doc_id)
    saved.add_turn_batch(tenant, _turns())
    saved.add_exchange_batch(tenant, _exchanges())
    app.save(saved)
    return doc_id


async def _drain(conn: Any, app: Any, duck: bool) -> int:
    """Advance the research projection over the emitted events."""
    embedder = FakeEmbedder({"embedding": {"dimensions": 1024}})
    if duck:
        reader = EventReader(
            None, app.mapper, app.recorder.events_table_name, recorder=app.recorder
        )
        embedder = ResearchEmbedder(None, embedder, gateway=conn)
        projection = ResearchProjection(
            None, CheckpointManager(gateway=conn), DLQHandler(gateway=conn), embedder, gateway=conn
        )
    else:
        reader = EventReader(conn, app.mapper, app.recorder.events_table_name)
        embedder = ResearchEmbedder(conn, embedder, model_revision="test-1024")
        projection = ResearchProjection(conn, CheckpointManager(conn), DLQHandler(conn), embedder)
    return await projection.catch_up(reader)


async def _tenant_row(conn: Any, tenant: UUID, sql: str, *args: object, duck: bool) -> Any:
    """One tenant-scoped read; Postgres FORCE RLS needs the tenant GUC."""
    if duck:
        return await conn.fetchrow(sql, str(tenant), *args)
    async with conn.acquire() as c, c.transaction():
        await c.execute(_PG_TENANT_SQL, str(tenant))
        return await c.fetchrow(sql, str(tenant), *args)


async def _count(conn: Any, tenant: UUID, sql: str, *args: object, duck: bool) -> int:
    row = await _tenant_row(conn, tenant, sql, *args, duck=duck)
    return int(row["n"]) if row is not None else 0


async def _doc_id_for_source(conn: Any, tenant: UUID, source: str, duck: bool) -> str | None:
    row = await _tenant_row(conn, tenant, _DOCS_SQL, source, duck=duck)
    return None if row is None else str(row["doc_id"])


async def _duck_stack(tmp_path: Path, name: str) -> tuple[DuckDbGateway, Any]:
    await run_migrations(duckdb_path_to_conn_string(tmp_path / name))
    gateway = await DuckDbGateway.connect(str(tmp_path / name))
    app = CorpusApplication(connection_string=str(tmp_path / (name + ".events")), backend="duckdb")
    return gateway, app


@pytest.mark.requires_postgres
async def test_reingest_same_file_no_dlq_pg(research_pool: Any) -> None:
    tenant = uuid4()
    source = "reingest-pg://hearing.md"
    corpus_doc = await _seed_corpus(research_pool, tenant, source, duck=False)
    app = get_app()
    research_doc = await _research_ingest(research_pool, app, tenant, source)
    await _drain(research_pool, app, duck=False)
    assert research_doc != UUID(corpus_doc)
    assert await _count(research_pool, tenant, _DLQ_SQL, duck=False) == 0
    assert await _doc_id_for_source(research_pool, tenant, source, duck=False) == str(research_doc)
    assert await _count(research_pool, tenant, _UNITS_SQL, str(corpus_doc), duck=False) == 0
    assert await _count(research_pool, tenant, _UNITS_SQL, str(research_doc), duck=False) == 2
    # A third ingest of the same source stays a no-op state-wise (the
    # twice-stable phase, Postgres leg).
    third = await _research_ingest(research_pool, app, tenant, source)
    await _drain(research_pool, app, duck=False)
    assert await _count(research_pool, tenant, _DLQ_SQL, duck=False) == 0
    assert await _doc_id_for_source(research_pool, tenant, source, duck=False) == str(third)
    assert await _count(research_pool, tenant, _UNITS_SQL, str(research_doc), duck=False) == 0
    assert await _count(research_pool, tenant, _UNITS_SQL, str(third), duck=False) == 2


async def test_reingest_same_file_no_dlq_duck(tmp_path: Path) -> None:
    gateway, app = await _duck_stack(tmp_path, "reingest-no-dlq.duckdb")
    try:
        tenant = uuid4()
        source = "reingest-duck://hearing.md"
        corpus_doc = await _seed_corpus(gateway, tenant, source, duck=True)
        research_doc = await _research_ingest(gateway, app, tenant, source)
        await _drain(gateway, app, duck=True)
        assert research_doc != UUID(corpus_doc)
        assert await _count(gateway, tenant, _DLQ_SQL, duck=True) == 0
        assert await _doc_id_for_source(gateway, tenant, source, duck=True) == str(research_doc)
        assert await _count(gateway, tenant, _UNITS_SQL, str(corpus_doc), duck=True) == 0
        assert await _count(gateway, tenant, _UNITS_SQL, str(research_doc), duck=True) == 2
    finally:
        await gateway.close()


async def test_reingest_preserves_exchange_reads_duck(tmp_path: Path) -> None:
    gateway, app = await _duck_stack(tmp_path, "reingest-exchange.duckdb")
    try:
        tenant = uuid4()
        source = "reingest-duck://exchange.md"
        await _seed_corpus(gateway, tenant, source, duck=True)
        research_doc = await _research_ingest(gateway, app, tenant, source)
        await _drain(gateway, app, duck=True)
        exchange = await gateway.fetchrow(
            "SELECT q_unit_ids, a_unit_ids, question_text, qa_text FROM research_exchanges"
            " WHERE tenant_id = $1 AND doc_id = $2::uuid",
            str(tenant),
            str(research_doc),
        )
        assert exchange is not None
        assert "panel concluded" in str(exchange["qa_text"])
        answer_unit = await gateway.fetchval(
            "SELECT unit_id FROM research_units WHERE tenant_id = $1"
            " AND doc_id = $2::uuid AND seq = 2",
            str(tenant),
            str(research_doc),
        )
        assert list(exchange["a_unit_ids"]) == [int(answer_unit)]
        linked = await gateway.fetchval(
            "SELECT exchange_id FROM research_units WHERE tenant_id = $1"
            " AND doc_id = $2::uuid AND seq = 2",
            str(tenant),
            str(research_doc),
        )
        assert linked is not None
    finally:
        await gateway.close()


async def test_reingest_twice_is_stable(tmp_path: Path) -> None:
    """A third research ingest of the same source stays a no-op.

    The Postgres leg rides inside ``test_reingest_same_file_no_dlq_pg``
    (lazy fixture loading inside a running event loop is unsupported).
    """
    gateway, app = await _duck_stack(tmp_path, "reingest-twice.duckdb")
    try:
        tenant = uuid4()
        source = "reingest-twice-duck://hearing.md"
        await _seed_corpus(gateway, tenant, source, duck=True)
        second = await _research_ingest(gateway, app, tenant, source)
        await _drain(gateway, app, duck=True)
        third = await _research_ingest(gateway, app, tenant, source)
        await _drain(gateway, app, duck=True)
        assert third != second
        assert await _count(gateway, tenant, _DLQ_SQL, duck=True) == 0
        assert await _doc_id_for_source(gateway, tenant, source, duck=True) == str(third)
        assert await _count(gateway, tenant, _UNITS_SQL, str(second), duck=True) == 0
        assert await _count(gateway, tenant, _UNITS_SQL, str(third), duck=True) == 2
    finally:
        await gateway.close()
