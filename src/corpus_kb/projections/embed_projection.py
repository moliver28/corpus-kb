"""EmbedChunksProjection — async vector embedding projection.

Subscribes to Document.ChunksAdded events. For each chunk, calls the
configured embedding provider (pgml in-database by default, Ollama as
fallback) and inserts the vector into chunks_vectors (pgvector).

The pre-spike code read a ``chunk_ids`` key the ChunksAdded event never
emitted and fell back to ``UUID(int=0)`` (todo-11 STEP 0, item (c) defect,
fixed): chunk ids are now DERIVED with the same deterministic uuid5 the
DocumentsProjection writes, so both projections agree without widening the
frozen legacy event payload.

Configurable embedding model via config (nomic-embed-text 768d or
qwen3-embedding:8b-q8_0 4096d). Vectors are derived data — never stored in
event payloads. All writes run in one transaction with the tenant GUC
(tenant_connection): chunks_vectors is FORCE-put under RLS.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections._drain import dispatch_notification, drain_sequence
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.ids import deterministic_chunk_id
from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder, aembed_batch
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)

PROJECTION_NAME = "EmbedChunksProjection"
BATCH_SIZE = 10


class EmbedChunksProjection:
    """Async projection: ChunksAdded event → embed → pgvector INSERT.

    Runs as a background task in the server event loop. Uses checkpoint
    tracking (global notification_id position) for crash recovery and the
    DLQ for failed embeddings.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: OllamaEmbedder | PgmlEmbedder,
        checkpoint: CheckpointManager,
        dlq: DLQHandler,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._checkpoint = checkpoint
        self._dlq = dlq
        self._running = False

    async def process_event(
        self,
        tenant_id: UUID,
        event_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        event_timestamp: datetime,
        last_sequence: int | None = None,
    ) -> None:
        """Process a single ChunksAdded event."""
        if event_type != "ChunksAdded":
            return

        chunks = payload.get("chunk_texts", [])
        if not chunks:
            return

        doc_id = str(payload.get("aggregate_id", ""))
        try:
            doc_uuid: UUID = UUID(doc_id)
        except ValueError:
            logger.error("ChunksAdded for non-UUID aggregate_id %r; skipping", doc_id)
            return

        try:
            # Batch embed (10 chunks at a time)
            for i in range(0, len(chunks), BATCH_SIZE):
                batch_texts = chunks[i : i + BATCH_SIZE]
                batch_start = i

                vectors = await aembed_batch(self._embedder, batch_texts)

                async with tenant_connection(self._pool, tenant_id) as conn:
                    for j, vector in enumerate(vectors):
                        chunk_id = deterministic_chunk_id(doc_uuid, batch_start + j)
                        await conn.execute(
                            """
                            INSERT INTO chunks_vectors
                            (chunk_id, tenant_id, vector, embedding_model)
                            VALUES ($1, $2, $3::vector, $4)
                            ON CONFLICT (chunk_id) DO UPDATE SET
                                vector = $3::vector,
                                embedding_model = $4,
                                embedded_at = NOW()
                            """,
                            str(chunk_id),
                            str(tenant_id),
                            str(vector),
                            self._embedder.model,
                        )

            # Update checkpoint after success
            await self._checkpoint.update_checkpoint(
                PROJECTION_NAME, tenant_id, event_id, event_timestamp, last_sequence
            )
            logger.debug("Embedded %d chunks for tenant %s", len(chunks), tenant_id)

        except Exception as exc:
            logger.error("Embed projection failed: %s", exc)
            await self._dlq.record_failure(
                PROJECTION_NAME,
                tenant_id,
                event_id,
                event_type,
                str(exc),
            )

    async def catch_up(self, reader: Any, tenant_id: UUID) -> int:
        """Drain the sequence until quiescent (bounded passes); event count."""

        async def handle(notification: Any) -> None:
            await dispatch_notification(
                notification, tenant_id, self._checkpoint, PROJECTION_NAME, self.process_event
            )

        return await drain_sequence(reader, tenant_id, self._checkpoint, PROJECTION_NAME, handle)

    async def run(self, tenant_id: UUID, reader: Any) -> None:
        """Main loop: poll the global notification_id sequence for events.

        ``reader`` is an EventReader bound to the eventsourcing Mapper.
        Events belonging to other tenants advance the shared checkpoint
        position without being projected.
        """
        self._running = True
        logger.info("EmbedChunksProjection started for tenant %s", tenant_id)

        while self._running:
            try:
                cp = await self._checkpoint.get_checkpoint(PROJECTION_NAME, tenant_id)
                last_sequence = int(cp["last_sequence"]) if cp and cp["last_sequence"] else 0

                notifications = await reader.read_since(last_sequence, limit=100)
                if not notifications:
                    await asyncio.sleep(1.0)  # No events, wait
                    continue

                for notification in notifications:
                    await dispatch_notification(
                        notification,
                        tenant_id,
                        self._checkpoint,
                        PROJECTION_NAME,
                        self.process_event,
                    )
            except Exception as exc:
                logger.error("Projection loop error: %s", exc)
                await asyncio.sleep(5.0)

    def stop(self) -> None:
        """Stop the projection loop."""
        self._running = False
        logger.info("EmbedChunksProjection stopping")


# Singleton

_embed_projection: EmbedChunksProjection | None = None


def get_embed_projection(
    pool: asyncpg.Pool | None = None,
    embedder: OllamaEmbedder | PgmlEmbedder | None = None,
) -> EmbedChunksProjection:
    global _embed_projection
    if _embed_projection is None:
        if pool is None or embedder is None:
            raise RuntimeError("EmbedChunksProjection requires pool + embedder")
        checkpoint = CheckpointManager(pool)
        dlq = DLQHandler(pool)
        _embed_projection = EmbedChunksProjection(pool, embedder, checkpoint, dlq)
    return _embed_projection


def set_embed_projection(proj: EmbedChunksProjection) -> None:
    global _embed_projection
    _embed_projection = proj


def reset_embed_projection() -> None:
    global _embed_projection
    _embed_projection = None
