"""Projection catch-up symmetry on DuckDB (storage portability, sp-projections-port).

Hermetic: the event store is the eventsourcing SQLite recorder file (phase-A
decision, program doc §7), the materialized tables live in a DuckDB file
behind the SqlGateway, and the embedder is the deterministic FakeEmbedder.
The proofs:

- incremental catch-up materializes exactly the new events' rows;
- replay-from-scratch converges to the same materialized state (idempotent);
- foreign-tenant events advance the shared checkpoint without materializing;
- a write-write conflict on the checkpoint row replays the whole unit.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import duckdb
import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.domain.aggregates import Document, Entity, Relation
from corpus_kb.domain.application import CorpusApplication
from corpus_kb.errors import ConfigError
from corpus_kb.projections._gateway import resolve_gateway
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.documents_projection import DocumentsProjection
from corpus_kb.projections.embed_projection import EmbedChunksProjection
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.rag.embedder import FakeEmbedder
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")
OTHER = UUID("00000000-0000-0000-0000-000000000002")

# Compact materialized-state columns. Timestamps are excluded on purpose:
# an upsert refreshes updated_at/embedded_at, and those are metadata, not
# materialized state.
_STATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "documents": ("doc_id", "tenant_id", "source", "source_type", "file_hash"),
    "chunks": ("chunk_id", "doc_id", "chunk_index"),
    "entities": ("entity_id", "name", "entity_type"),
    "relations": ("relation_id", "source_entity_id", "target_entity_id", "relation_type"),
    "chunks_vectors": ("chunk_id", "tenant_id", "embedding_model"),
}


async def _migrated_gateway(tmp_path: Path, name: str) -> tuple[DuckDbGateway, Path]:
    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path)), path


def _app(tmp_path: Path) -> CorpusApplication:
    return CorpusApplication(connection_string=str(tmp_path / "events.db"), backend="duckdb")


def _reader(app: CorpusApplication) -> EventReader:
    """Recorder-based reader: the DuckDB event store is the SQLite file."""
    return EventReader(None, app.mapper, app.recorder.events_table_name, recorder=app.recorder)


def _save_document(app: CorpusApplication, tenant: UUID, source: str, chunks: list[str]) -> UUID:
    doc = Document(
        tenant_id=tenant,
        source=source,
        source_type="transcript",
        file_hash=uuid.uuid4().hex,
    )
    doc.add_chunks(chunk_count=len(chunks), chunk_texts=chunks)
    app.save(doc)
    return UUID(str(doc.id))


async def _snapshot(gateway: DuckDbGateway) -> dict[str, list[tuple[object, ...]]]:
    """Deterministic state read: one column set per table, ordered by its key."""
    state: dict[str, list[tuple[object, ...]]] = {}
    for table, columns in _STATE_COLUMNS.items():
        rows = await gateway.fetch(f"SELECT {', '.join(columns)} FROM {table} ORDER BY 1")
        state[table] = [tuple(row.values()) for row in rows]
    return state


async def _last_sequence(checkpoint: CheckpointManager, name: str, tenant: UUID) -> int | None:
    row = await checkpoint.get_checkpoint(name, tenant)
    return int(row["last_sequence"]) if row and row["last_sequence"] else None


async def _catch_up(
    gateway: DuckDbGateway, app: CorpusApplication, tenant: UUID
) -> tuple[DocumentsProjection, EmbedChunksProjection, CheckpointManager]:
    checkpoint = CheckpointManager(gateway=gateway)
    dlq = DLQHandler(gateway=gateway)
    embedder = FakeEmbedder({"embedding": {"dimensions": 2560}})
    docs = DocumentsProjection(None, checkpoint, dlq, gateway=gateway)
    embeds = EmbedChunksProjection(None, embedder, checkpoint, dlq, gateway=gateway)
    reader = _reader(app)
    await docs.catch_up(reader, tenant)
    await embeds.catch_up(reader, tenant)
    return docs, embeds, checkpoint


async def test_catchup_is_symmetric_and_idempotent(tmp_path: Path) -> None:
    gateway, _ = await _migrated_gateway(tmp_path, "symmetry.duckdb")
    app = _app(tmp_path)
    try:
        # Phase 1: two documents (one foreign-tenant), an entity, a relation.
        _save_document(app, TENANT, "sym://one", ["Moderator: How?", "P: Fine, mostly."])
        entity = Entity(tenant_id=TENANT, name="retry-loop", entity_type="concept")
        app.save(entity)
        relation = Relation(
            tenant_id=TENANT,
            source_entity_id=UUID(str(entity.id)),
            target_entity_id=UUID(str(entity.id)),
            relation_type="related_to",
        )
        app.save(relation)
        _save_document(app, OTHER, "sym://foreign", [])
        head_after_phase1 = app.recorder.max_notification_id()

        docs, embeds, checkpoint = await _catch_up(gateway, app, TENANT)
        phase1 = await _snapshot(gateway)

        assert [len(phase1[t]) for t in _STATE_COLUMNS] == [1, 2, 1, 1, 2]
        assert (
            await _last_sequence(checkpoint, "DocumentsProjection", TENANT) == head_after_phase1
        ), "foreign-tenant events must advance the shared sequence"
        foreign = [row for row in phase1["documents"] if row[2] == "sym://foreign"]
        assert foreign == [], "foreign-tenant events must not materialize"

        # Phase 2: one more tenant document, then incremental catch-up.
        _save_document(app, TENANT, "sym://two", ["P2: Same experience here."])
        head_after_phase2 = app.recorder.max_notification_id()
        assert head_after_phase2 > head_after_phase1

        await docs.catch_up(_reader(app), TENANT)
        await embeds.catch_up(_reader(app), TENANT)
        phase2 = await _snapshot(gateway)
        assert await _last_sequence(checkpoint, "DocumentsProjection", TENANT) == head_after_phase2
        assert [len(phase2[t]) for t in _STATE_COLUMNS] == [2, 3, 1, 1, 3]
        # Existing rows are byte-stable across the incremental pass (the
        # global chunk_id ordering interleaves documents, so compare sets).
        for table in _STATE_COLUMNS:
            assert set(phase1[table]) <= set(phase2[table]), table

        # Replay from scratch: wipe materialized state + checkpoints, re-drain.
        for table in (
            "chunks_vectors",
            "chunks",
            "relations",
            "entities",
            "documents",
            "projection_checkpoints",
        ):
            await gateway.execute(f"DELETE FROM {table}")
        rebuilt_docs, rebuilt_embeds, rebuilt_checkpoint = await _catch_up(gateway, app, TENANT)
        rebuilt = await _snapshot(gateway)
        assert rebuilt == phase2, "replay-from-scratch must equal incremental state"
        assert (
            await _last_sequence(rebuilt_checkpoint, "DocumentsProjection", TENANT)
            == head_after_phase2
        )
        # A quiet second pass changes nothing (catch-up is idempotent).
        await rebuilt_docs.catch_up(_reader(app), TENANT)
        await rebuilt_embeds.catch_up(_reader(app), TENANT)
        assert await _snapshot(gateway) == rebuilt
    finally:
        await gateway.close()


async def test_checkpoint_conflict_replays_the_whole_unit(tmp_path: Path) -> None:
    gateway, path = await _migrated_gateway(tmp_path, "conflict.duckdb")
    rival = duckdb.connect(str(path), config={"default_null_order": "postgres"})
    try:
        first_seen = datetime.now(tz=UTC)
        await gateway.execute(
            "INSERT INTO projection_checkpoints (projection_name, tenant_id, "
            "last_event_id, last_event_timestamp, last_sequence) "
            "VALUES ($1, $2, $3, $4, 1)",
            "DocumentsProjection",
            str(TENANT),
            str(uuid4()),
            first_seen,
        )
        rival.execute("BEGIN TRANSACTION")
        rival.execute(
            "UPDATE projection_checkpoints SET last_sequence = 999 WHERE tenant_id = ?",
            [str(TENANT)],
        )

        async def release_rival() -> None:
            # Attempts 1-4 land before this rollback; the replayed unit wins.
            await asyncio.sleep(0.2)
            rival.execute("ROLLBACK")

        release = asyncio.create_task(release_rival())
        checkpoint = CheckpointManager(gateway=gateway)
        await checkpoint.update_checkpoint(
            "DocumentsProjection", TENANT, uuid4(), datetime.now(tz=UTC), 2
        )
        await release
        final = await gateway.fetchval(
            "SELECT last_sequence FROM projection_checkpoints WHERE tenant_id = $1", str(TENANT)
        )
        assert final == 2, "the replayed unit must overwrite the rival value"
    finally:
        rival.close()
        await gateway.close()


async def test_resolve_gateway_requires_a_driver() -> None:
    with pytest.raises(ConfigError):
        resolve_gateway(None, None)
