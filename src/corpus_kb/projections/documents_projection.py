"""DocumentsProjection — projects events into documents, chunks, entities, relations tables.

Subscribes to:
  - Document.Ingested → upsert into documents
  - Document.ChunksAdded → insert into chunks (text only, no vectors)
  - Entity.Created → insert into entities
  - Relation.Created → insert into relations

All writes run inside one transaction per event with the tenant GUC set
(tenant_connection), because these tables are FORCE-put under row-level
security. Chunk rows get deterministic ids (uuid5 of document id + position)
so replays are idempotent — the pre-spike code wrote UUID(int=0) for every
chunk (todo-11 STEP 0, item (c) defect, fixed).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections._drain import dispatch_notification, drain_sequence
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.ids import deterministic_chunk_id
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)

PROJECTION_NAME = "DocumentsProjection"


class DocumentsProjection:
    """Projects domain events into read-model tables."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        checkpoint: CheckpointManager,
        dlq: DLQHandler,
    ) -> None:
        self._pool = pool
        self._checkpoint = checkpoint
        self._dlq = dlq

    async def process_event(
        self,
        tenant_id: UUID,
        event_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        event_timestamp: datetime,
        last_sequence: int | None = None,
    ) -> None:
        """Dispatch event to the appropriate projection method.

        ``last_sequence`` (when given) records the global notification_id
        position in the same checkpoint write.
        """
        try:
            if event_type == "Ingested":
                await self._project_document(tenant_id, payload)
            elif event_type == "ChunksAdded":
                await self._project_chunks(tenant_id, payload)
            elif event_type == "Created" and "entity_type" in payload:
                await self._project_entity(tenant_id, payload)
            elif event_type == "Created" and "source_entity_id" in payload:
                await self._project_relation(tenant_id, payload)

            await self._checkpoint.update_checkpoint(
                PROJECTION_NAME, tenant_id, event_id, event_timestamp, last_sequence
            )
        except Exception as exc:
            logger.error("DocumentsProjection failed: %s", exc)
            await self._dlq.record_failure(
                PROJECTION_NAME, tenant_id, event_id, event_type, str(exc)
            )

    async def catch_up(self, reader: Any, tenant_id: UUID) -> int:
        """Drain the sequence until quiescent (bounded passes); event count."""

        async def handle(notification: Any) -> None:
            await dispatch_notification(
                notification, tenant_id, self._checkpoint, PROJECTION_NAME, self.process_event
            )

        return await drain_sequence(reader, tenant_id, self._checkpoint, PROJECTION_NAME, handle)

    async def run(self, tenant_id: UUID, reader: Any) -> None:
        """Catch-up loop over the global notification_id sequence.

        ``reader`` is an EventReader bound to the eventsourcing Mapper (the
        lib's events table is topic/state + notification_id bigserial).
        Events for OTHER tenants advance the shared sequence but are skipped
        for projection — the checkpoint records the global position either way,
        so no event is ever revisited or skipped.
        """
        logger.info("DocumentsProjection started for tenant %s", tenant_id)
        while True:
            cp = await self._checkpoint.get_checkpoint(PROJECTION_NAME, tenant_id)
            last_sequence = int(cp["last_sequence"]) if cp and cp["last_sequence"] else 0
            notifications = await reader.read_since(last_sequence, limit=200)
            if not notifications:
                await asyncio.sleep(1.0)
                continue
            for notification in notifications:
                await dispatch_notification(
                    notification, tenant_id, self._checkpoint, PROJECTION_NAME, self.process_event
                )

    async def _project_document(self, tenant_id: UUID, payload: dict[str, Any]) -> None:
        """Upsert into the documents table."""
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO documents
                (doc_id, tenant_id, source, source_type, file_size, file_hash,
                 language, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (tenant_id, source) DO UPDATE SET
                    source_type = $4,
                    file_size = $5,
                    file_hash = $6,
                    language = $7,
                    metadata = $8,
                    updated_at = NOW()
                """,
                str(payload.get("aggregate_id", "")),
                str(tenant_id),
                payload.get("source", ""),
                payload.get("source_type", "text"),
                payload.get("file_size"),
                payload.get("file_hash"),
                payload.get("language"),
                json.dumps(payload.get("metadata", {})),
            )

    async def _project_chunks(self, tenant_id: UUID, payload: dict[str, Any]) -> None:
        """Insert chunk rows with deterministic ids (fixes UUID(int=0) defect)."""
        chunk_texts = payload.get("chunk_texts", [])
        if not chunk_texts:
            return

        doc_id = str(payload.get("aggregate_id", ""))
        try:
            doc_uuid: UUID = UUID(doc_id)
        except ValueError:
            logger.error("ChunksAdded for non-UUID aggregate_id %r; skipping", doc_id)
            return

        async with tenant_connection(self._pool, tenant_id) as conn:
            for i, text in enumerate(chunk_texts):
                await conn.execute(
                    """
                    INSERT INTO chunks
                    (chunk_id, tenant_id, doc_id, chunk_index, text)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
                    """,
                    str(deterministic_chunk_id(doc_uuid, i)),
                    str(tenant_id),
                    doc_id,
                    i,
                    text,
                )

    async def _project_entity(self, tenant_id: UUID, payload: dict[str, Any]) -> None:
        """Insert into the entities table."""
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO entities
                (entity_id, tenant_id, name, entity_type, metadata)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
                """,
                str(payload.get("aggregate_id", "")),
                str(tenant_id),
                payload.get("name", ""),
                payload.get("entity_type", "concept"),
                json.dumps(payload.get("metadata", {})),
            )

    async def _project_relation(self, tenant_id: UUID, payload: dict[str, Any]) -> None:
        """Insert into the relations table."""
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO relations
                (relation_id, tenant_id, source_entity_id, target_entity_id,
                 relation_type, weight, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT
                (tenant_id, source_entity_id, target_entity_id, relation_type)
                DO NOTHING
                """,
                str(payload.get("aggregate_id", "")),
                str(tenant_id),
                str(payload.get("source_entity_id", "")),
                str(payload.get("target_entity_id", "")),
                payload.get("relation_type", "related_to"),
                payload.get("weight", 1.0),
                json.dumps(payload.get("metadata", {})),
            )


# Singleton

_docs_projection: DocumentsProjection | None = None


def get_documents_projection(
    pool: asyncpg.Pool | None = None,
) -> DocumentsProjection:
    global _docs_projection
    if _docs_projection is None:
        if pool is None:
            raise RuntimeError("DocumentsProjection requires an asyncpg pool")
        checkpoint = CheckpointManager(pool)
        dlq = DLQHandler(pool)
        _docs_projection = DocumentsProjection(pool, checkpoint, dlq)
    return _docs_projection


def set_documents_projection(proj: DocumentsProjection) -> None:
    global _docs_projection
    _docs_projection = proj


def reset_documents_projection() -> None:
    global _docs_projection
    _docs_projection = None
