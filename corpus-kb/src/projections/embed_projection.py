"""EmbedChunksProjection — async vector embedding projection.

Subscribes to ChunksAdded events. For each chunk, calls the embedding
service (Ollama) and inserts the vector into chunks_vectors (pgvector).

Configurable embedding model via config (nomic-embed-text 768d or
qwen3-embedding:8b-q8_0 4096d). Vectors are derived data — never
stored in event payloads.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

from projections.checkpoint import CheckpointManager
from projections.dlq import DLQHandler
from src.rag.embedder import OllamaEmbedder

logger = logging.getLogger(__name__)

PROJECTION_NAME = "EmbedChunksProjection"
BATCH_SIZE = 10


class EmbedChunksProjection:
    """Async projection: ChunksAdded event → embed → pgvector INSERT.

    Runs as a background task in the server event loop. Uses checkpoint
    tracking for crash recovery and DLQ for failed embeddings.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: OllamaEmbedder,
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
        event_timestamp: str,
    ) -> None:
        """Process a single ChunksAdded event."""
        if event_type != "ChunksAdded":
            return

        chunks = payload.get("chunk_texts", [])
        chunk_ids = payload.get("chunk_ids", [])

        if not chunks:
            return

        try:
            # Batch embed (10 chunks at a time)
            for i in range(0, len(chunks), BATCH_SIZE):
                batch_texts = chunks[i : i + BATCH_SIZE]
                batch_ids = chunk_ids[i : i + BATCH_SIZE] if chunk_ids else []

                vectors = self._embedder.embed_batch(batch_texts)

                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "SELECT set_config('app.current_tenant_id', $1, true)",
                        str(tenant_id),
                    )
                    for j, (text, vector) in enumerate(
                        zip(batch_texts, vectors, strict=True)
                    ):
                        chunk_id = (
                            batch_ids[j] if j < len(batch_ids) else str(UUID(int=0))
                        )
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
                            chunk_id,
                            str(tenant_id),
                            str(vector),
                            self._embedder.model,
                        )

            # Update checkpoint after success
            await self._checkpoint.update_checkpoint(
                PROJECTION_NAME, tenant_id, event_id, event_timestamp
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

    async def run(self, tenant_id: UUID) -> None:
        """Main loop: poll for events and process them."""
        self._running = True
        logger.info("EmbedChunksProjection started for tenant %s", tenant_id)

        while self._running:
            try:
                cp = await self._checkpoint.get_checkpoint(PROJECTION_NAME, tenant_id)
                last_ts = cp["last_event_timestamp"] if cp else None

                events = await self._checkpoint.get_events_since(
                    tenant_id, last_ts, limit=100
                )

                if not events:
                    await asyncio.sleep(1.0)  # No events, wait
                    continue

                for event in events:
                    await self.process_event(
                        tenant_id,
                        event["event_id"],
                        event["event_type"],
                        event["payload"],
                        str(event["created_at"]),
                    )
            except Exception as exc:
                logger.error("Projection loop error: %s", exc)
                await asyncio.sleep(5.0)

    def stop(self) -> None:
        """Stop the projection loop."""
        self._running = False
        logger.info("EmbedChunksProjection stopping")


# Singleton

_embed_projection: Optional[EmbedChunksProjection] = None


def get_embed_projection(
    pool: Optional[asyncpg.Pool] = None,
    embedder: Optional[OllamaEmbedder] = None,
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
