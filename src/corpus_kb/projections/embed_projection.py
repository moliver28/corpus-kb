"""EmbedChunksProjection — async vector embedding projection.

Subscribes to Document.ChunksAdded events. For each chunk, calls the
configured embedding provider (pgml in-database by default, Ollama as
fallback) and inserts the vector into chunks_vectors (pgvector).

The pre-spike code read a ``chunk_ids`` key the ChunksAdded event never
emitted and fell back to ``UUID(int=0)`` (todo-11 STEP 0, item (c) defect,
fixed): chunk ids are now DERIVED with the same deterministic uuid5 the
DocumentsProjection writes, so both projections agree without widening the
frozen legacy event payload.

Configurable embedding model via config (default qwen3-embedding:4b at
its native 2560 dims, stored as halfvec). Vectors are derived data — never
stored in event payloads. All writes run in one transaction with the tenant
context (the ``SqlGateway`` scope: tenant GUC on Postgres, BEGIN..COMMIT on
DuckDB).

Vector storage is the one per-backend SQL twin in this module: Postgres
binds the halfvec text form (``$3::halfvec``), DuckDB the native float
array (``$3::FLOAT[2560]`` — the migration-000/025 column regime).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections._drain import (
    dispatch_notification,
    drain_sequence,
    legacy_owner_resolver,
)
from corpus_kb.projections._gateway import resolve_gateway
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.ids import deterministic_chunk_id
from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder, aembed_batch
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import vector_literal
from corpus_kb.storage.gateway import SqlGateway

logger = logging.getLogger(__name__)

PROJECTION_NAME = "EmbedChunksProjection"
BATCH_SIZE = 10

#: The DuckDB ``chunks_vectors.embedding`` width (migrations 000 + 025).
DUCKDB_EMBEDDING_DIMS = 2560


class EmbedChunksProjection:
    """Async projection: ChunksAdded event → embed → vector INSERT.

    Runs as a background task in the server event loop. Uses checkpoint
    tracking (global notification_id position) for crash recovery and the
    DLQ for failed embeddings.
    """

    def __init__(
        self,
        pool: asyncpg.Pool | None,
        embedder: OllamaEmbedder | PgmlEmbedder,
        checkpoint: CheckpointManager,
        dlq: DLQHandler,
        gateway: SqlGateway | None = None,
    ) -> None:
        self._gateway = resolve_gateway(pool, gateway)
        self._embedder = embedder
        self._checkpoint = checkpoint
        self._dlq = dlq
        self._running = False
        # The one per-backend fragment: the cast and the bound value change
        # together (halfvec takes the text form; FLOAT[k] takes the list).
        if isinstance(self._gateway, DuckDbGateway):
            self._vector_cast = f"$3::FLOAT[{DUCKDB_EMBEDDING_DIMS}]"
        else:
            self._vector_cast = "$3::halfvec"

    def _vector_arg(self, vector: list[float]) -> Any:
        """Bind form for one embedding.

        Postgres keeps the halfvec text form (byte-identical to the
        pre-port call site). DuckDB takes the canonical compact
        :func:`duckdb_support.vector_literal` string behind the
        ``FLOAT[k]`` cast: a 2560-float Python list parameter costs
        seconds per statement to bind, the string form milliseconds
        (sp-duckdb-search binding measurement).
        """
        if isinstance(self._gateway, DuckDbGateway):
            return vector_literal(vector)
        return str(vector)

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

                async with self._gateway.transaction(str(tenant_id)) as tx:
                    for j, vector in enumerate(vectors):
                        chunk_id = deterministic_chunk_id(doc_uuid, batch_start + j)
                        await tx.execute(
                            f"""
                            INSERT INTO chunks_vectors
                            (chunk_id, tenant_id, embedding, embedding_model)
                            VALUES ($1, $2, {self._vector_cast}, $4)
                            ON CONFLICT (chunk_id) DO UPDATE SET
                                embedding = {self._vector_cast},
                                embedding_model = $4,
                                embedded_at = NOW()
                            """,
                            str(chunk_id),
                            str(tenant_id),
                            self._vector_arg(vector),
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
                notification,
                tenant_id,
                self._checkpoint,
                PROJECTION_NAME,
                self.process_event,
                resolve_owner=legacy_owner_resolver(reader),
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
                        resolve_owner=legacy_owner_resolver(reader),
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
