"""DocumentsProjection — projects events into documents, chunks, entities, relations tables.

Subscribes to:
  - DocumentIngested → INSERT into documents
  - ChunksAdded → INSERT into chunks (text only, no vectors)
  - EntityCreated → INSERT into entities
  - RelationCreated → INSERT into relations

Uses asyncpg + SET LOCAL for RLS enforcement. Idempotent (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

from projections.checkpoint import CheckpointManager
from projections.dlq import DLQHandler

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
        event_timestamp: str,
    ) -> None:
        """Dispatch event to the appropriate projection method."""
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
                PROJECTION_NAME, tenant_id, event_id, event_timestamp
            )
        except Exception as exc:
            logger.error("DocumentsProjection failed: %s", exc)
            await self._dlq.record_failure(
                PROJECTION_NAME, tenant_id, event_id, event_type, str(exc)
            )

    async def _project_document(
        self, tenant_id: UUID, payload: dict[str, Any]
    ) -> None:
        """INSERT into documents table."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
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

    async def _project_chunks(
        self, tenant_id: UUID, payload: dict[str, Any]
    ) -> None:
        """INSERT into chunks table (text only, no vectors)."""
        chunk_texts = payload.get("chunk_texts", [])
        if not chunk_texts:
            return

        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            for i, text in enumerate(chunk_texts):
                chunk_id = str(UUID(int=0))  # placeholder — real ID from event
                doc_id = str(payload.get("aggregate_id", ""))
                await conn.execute(
                    """
                    INSERT INTO chunks
                    (chunk_id, tenant_id, doc_id, chunk_index, text)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
                    """,
                    chunk_id,
                    str(tenant_id),
                    doc_id,
                    i,
                    text,
                )

    async def _project_entity(
        self, tenant_id: UUID, payload: dict[str, Any]
    ) -> None:
        """INSERT into entities table."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
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

    async def _project_relation(
        self, tenant_id: UUID, payload: dict[str, Any]
    ) -> None:
        """INSERT into relations table."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
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

_docs_projection: Optional[DocumentsProjection] = None


def get_documents_projection(
    pool: Optional[asyncpg.Pool] = None,
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