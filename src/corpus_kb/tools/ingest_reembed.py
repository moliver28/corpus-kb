"""Degraded + stale chunk repair for the ingest pipeline (zero-drift 3B split).

Moved byte-identical from ``ingest_common``: ``reembed_degraded`` — the
Stage-3 handler behind ``corpus-kb ingest-reembed`` and the MCP/HTTP
reembed surfaces. ``ingest_common`` re-exports it. The vector-write
config reads (model, dimensions, matryoshka dim) come from the shared
``vector_write_config`` so a repair and a fresh ingest read them
identically. Writes go through the SqlGateway (``ingest_sql`` carries the
per-backend statement variants); the Postgres SQL text stays
byte-identical.

Config-v2 extension: the repair ALSO targets STALE rows —
``chunks_vectors.embedding IS NULL`` — which is the state migration 025
leaves behind when the embedding model changes (the old model's vectors
cannot convert). Degraded (all-zero-flagged) behavior is unchanged; the
summary reports the counts per class.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import asyncpg

from ..utils.models import Chunk
from .ingest_cfg import matryoshka_slice, vector_write_config
from .ingest_embed import _all_zero, build_embedding_input, embed_chunks
from .ingest_sql import (
    COLUMNS_PROBE_DUCKDB,
    COLUMNS_PROBE_PG,
    DEGRADED_CLEAR_DUCKDB,
    DEGRADED_CLEAR_PG,
    REPAIR_SQL_DUCKDB,
    REPAIR_SQL_PG,
    SCAN_SQL_DUCKDB,
    SCAN_SQL_PG,
    vector_write_params,
)
from .ingest_store import DEFAULT_TENANT_ID, as_gateway, gateway_backend

if TYPE_CHECKING:
    from ..storage.gateway import SqlGateway

logger = logging.getLogger(__name__)


async def reembed_degraded(
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object],
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int | None = None,
    doc_id: str | None = None,
) -> dict[str, object]:
    """Re-embed chunks whose stored vector is degraded or stale (Stage 3).

    Targets rows where ``chunks_vectors.degraded`` or ``chunks.degraded``
    is TRUE (the migration-022 flags) OR ``chunks_vectors.embedding`` IS
    NULL (the migration-025 stale state after a model switch), rebuilds
    each embedding input with ``build_embedding_input`` (the exact bytes a
    fresh ingest would embed), and on a non-zero result writes the repaired
    vector and CLEARS the degraded flags. All-zero output leaves the row
    flagged — a repair that produced the degraded-mode marker is a failure,
    never a silent "fix".

    Requires migration 022; on a pre-migration database the degraded columns
    do not exist, so this returns ``{"status": "unavailable", ...}`` instead
    of crashing. CLI/surface registration is Stage 5; this is the handler.

    Returns an honest ``{status, scanned, fixed, failed}`` summary; the
    ``stale`` count (repaired rows that were stale rather than degraded)
    is added only when non-zero, per the conditional-envelope contract.
    """
    gateway = as_gateway(pg_pool)
    pool = pg_pool if isinstance(pg_pool, asyncpg.Pool) else None
    is_duckdb = gateway_backend(gateway) == "duckdb"
    async with gateway.transaction(tenant_id) as tx:
        present = await tx.fetchval(
            COLUMNS_PROBE_DUCKDB if is_duckdb else COLUMNS_PROBE_PG,
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
        rows = await tx.fetch(
            SCAN_SQL_DUCKDB if is_duckdb else SCAN_SQL_PG,
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
        chunks, config, pool=pool, text_override=text_override, meta=embed_meta
    )
    if embed_err is not None:
        logger.warning("reembed_degraded: %s", embed_err)

    embedding_model, dimensions, v1024_dim = vector_write_config(config)

    stale_by_chunk = {row["chunk_id"] for row in rows if row["was_stale"]}
    fixed, failed, stale = await _write_repairs(
        gateway,
        tenant_id=tenant_id,
        chunks=chunks,
        stale_by_chunk=stale_by_chunk,
        embedding_model=embedding_model,
        dimensions=dimensions,
        v1024_dim=v1024_dim,
        is_duckdb=is_duckdb,
    )
    summary: dict[str, object] = {
        "status": "success",
        "scanned": len(chunks),
        "fixed": fixed,
        "failed": failed,
    }
    if stale > 0:
        summary["stale"] = stale
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


async def _write_repairs(
    gateway: SqlGateway,
    *,
    tenant_id: str,
    chunks: list[Chunk],
    stale_by_chunk: set[str],
    embedding_model: str,
    dimensions: int,
    v1024_dim: int | None,
    is_duckdb: bool,
) -> tuple[int, int, int]:
    """Write the repaired vectors; return (fixed, failed, stale) counts.

    One transaction scope, tenant context first. The UPDATEs additionally
    pin tenant_id explicitly so a missing/lost tenant context can never
    match (or, worse, clear flags on) another tenant's rows. All-zero
    output leaves the row flagged and counts as failed — a repair that
    produced the degraded-mode marker is a failure, never a silent "fix".
    """
    fixed = 0
    failed = 0
    stale = 0
    repair_sql = REPAIR_SQL_DUCKDB if is_duckdb else REPAIR_SQL_PG
    clear_sql = DEGRADED_CLEAR_DUCKDB if is_duckdb else DEGRADED_CLEAR_PG
    async with gateway.transaction(tenant_id) as tx:
        for chunk in chunks:
            if chunk.embedding is None or _all_zero([chunk.embedding]):
                failed += 1
                continue
            v1024 = matryoshka_slice(chunk.embedding, v1024_dim) if v1024_dim is not None else None
            embedding_param, v1024_param = vector_write_params(chunk.embedding, v1024)
            await tx.execute(
                repair_sql,
                chunk.chunk_id,
                embedding_param,
                v1024_param,
                embedding_model,
                dimensions,
                tenant_id,
            )
            await tx.execute(clear_sql, chunk.chunk_id, tenant_id)
            fixed += 1
            if chunk.chunk_id in stale_by_chunk:
                stale += 1
    return fixed, failed, stale
