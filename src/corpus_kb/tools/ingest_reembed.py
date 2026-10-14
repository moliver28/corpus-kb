"""Degraded-chunk repair for the ingest pipeline (zero-drift Wave 3B split).

Moved byte-identical from ``ingest_common``: ``reembed_degraded`` — the
Stage-3 handler behind ``corpus-kb ingest-reembed`` and the MCP/HTTP
reembed surfaces. ``ingest_common`` re-exports it. The vector-write
config reads (model, dimensions, matryoshka dim) come from the shared
``vector_write_config`` so a repair and a fresh ingest read them
identically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..utils.models import Chunk
from .ingest_cfg import matryoshka_slice, vector_write_config
from .ingest_embed import _all_zero, build_embedding_input, embed_chunks
from .ingest_store import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


async def reembed_degraded(
    pg_pool: asyncpg.Pool,
    config: dict[str, object],
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int | None = None,
    doc_id: str | None = None,
) -> dict[str, object]:
    """Re-embed chunks whose stored vector is flagged degraded (Stage 3).

    Targets rows where ``chunks_vectors.degraded`` or ``chunks.degraded`` is
    TRUE (the migration-022 flags), rebuilds each embedding input with
    ``build_embedding_input`` (the exact bytes a fresh ingest would embed),
    and on a non-zero result writes the repaired vector and CLEARS the
    degraded flags. All-zero output leaves the row flagged — a repair that
    produced the degraded-mode marker is a failure, never a silent "fix".

    Requires migration 022; on a pre-migration database the degraded columns
    do not exist, so this returns ``{"status": "unavailable", ...}`` instead
    of crashing. CLI/surface registration is Stage 5; this is the handler.

    Returns an honest ``{scanned, fixed, failed}`` summary.
    """
    async with pg_pool.acquire() as conn, conn.transaction():
        # set_config FIRST inside the transaction: a LOCAL GUC issued on an
        # autocommit connection evaporates with its statement, so the reads
        # below would run GUC-less and match zero rows on FORCE-RLS
        # deployments (the ingest_store ledger-method pattern).
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
        present = await conn.fetchval(
            """
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY($1::text[])
              AND column_name = 'degraded'
            """,
            ["chunks", "chunks_vectors"],
        )
        if present != 2:
            return {
                "status": "unavailable",
                "reason": (
                    "migration 022 not applied: chunks.degraded / "
                    "chunks_vectors.degraded missing; run corpus-kb setup"
                ),
                "scanned": 0,
                "fixed": 0,
                "failed": 0,
            }
        rows = await conn.fetch(
            """
            SELECT cv.chunk_id::text AS chunk_id, c.doc_id::text AS doc_id,
                   c.text, c.context_blurb
            FROM chunks_vectors cv
            JOIN chunks c ON c.chunk_id = cv.chunk_id
            WHERE cv.tenant_id = $1 AND (cv.degraded = TRUE OR c.degraded = TRUE)
              AND ($2::uuid IS NULL OR c.doc_id = $2::uuid)
            ORDER BY cv.embedded_at NULLS LAST
            LIMIT $3
            """,
            tenant_id,
            doc_id,
            limit if limit is not None else 2**31 - 1,
        )
    if not rows:
        return {"status": "success", "scanned": 0, "fixed": 0, "failed": 0}

    # Reuse the pipeline's embed path (provider + zero-vector fallback chain)
    # by rebuilding minimal Chunk objects; text_override keys by chunk_id.
    chunks = [
        Chunk(
            chunk_id=row["chunk_id"],
            document_id=row["doc_id"],
            text=row["text"],
            source_type="text",
            context_blurb=row["context_blurb"],
        )
        for row in rows
    ]
    text_override = {c.chunk_id: build_embedding_input(c.text, c.context_blurb) for c in chunks}
    # meta passthrough: a repair re-embeds WITHOUT the document, so when late
    # chunking is the configured technique the repaired chunk lands in the
    # pooled vector space while its siblings stay late-chunked — surface the
    # mode/note in the summary instead of silently mixing spaces.
    embed_meta: dict[str, object] = {}
    degraded, embed_err = await embed_chunks(
        chunks, config, pool=pg_pool, text_override=text_override, meta=embed_meta
    )
    if embed_err is not None:
        logger.warning("reembed_degraded: %s", embed_err)

    embedding_model, dimensions, v1024_dim = vector_write_config(config)

    fixed = 0
    failed = 0
    async with pg_pool.acquire() as conn, conn.transaction():
        # Same GUC-in-transaction contract as the scan block above; the
        # UPDATEs additionally pin tenant_id explicitly so a missing/lost
        # tenant context can never match (or, worse, clear flags on) another
        # tenant's rows.
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
        for chunk in chunks:
            if chunk.embedding is None or _all_zero([chunk.embedding]):
                failed += 1
                continue
            vector_str = "[" + ",".join(str(v) for v in chunk.embedding) + "]"
            v1024: str | None = None
            if v1024_dim is not None:
                v1024 = (
                    "["
                    + ",".join(str(v) for v in matryoshka_slice(chunk.embedding, v1024_dim))
                    + "]"
                )
            await conn.execute(
                """
                UPDATE chunks_vectors
                SET vector = $2::vector,
                    vector_1024 = COALESCE($3::vector, chunks_vectors.vector_1024),
                    embedding_model = $4, dimensions = $5,
                    embedded_at = NOW(), degraded = FALSE
                WHERE chunk_id = $1::uuid AND tenant_id = $6
                """,
                chunk.chunk_id,
                vector_str,
                v1024,
                embedding_model,
                dimensions,
                tenant_id,
            )
            await conn.execute(
                "UPDATE chunks SET degraded = FALSE WHERE chunk_id = $1::uuid AND tenant_id = $2",
                chunk.chunk_id,
                tenant_id,
            )
            fixed += 1
    summary: dict[str, object] = {
        "status": "success",
        "scanned": len(chunks),
        "fixed": fixed,
        "failed": failed,
    }
    if embed_meta.get("embedding_mode") is not None:
        summary["embedding_mode"] = embed_meta["embedding_mode"]
    if embed_meta.get("embedding_note") is not None:
        summary["embedding_note"] = embed_meta["embedding_note"]
    if degraded and embed_err is not None:
        # The write path above already judged each vector per-chunk; this
        # flag reports the PRIMARY provider's health honestly (a fallback
        # repair still succeeds but the primary needs attention).
        summary["degraded"] = True
        summary["warning"] = embed_err
    return summary
