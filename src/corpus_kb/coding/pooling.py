"""Pooling module: keyword hits, vector similarity, chunk_signals materialization.

The vector math lives in :mod:`pooling_math` and the similarity-pooling
body in :mod:`pooling_similarity` (pure moves for the 250-line rule); this
module keeps the tenant-scope entry points and re-exports
``cosine_similarity`` so the import surface is unchanged. The three public
functions all resolve ``tenant_scope`` through THIS module's globals —
tests patch it here (tests/test_wave1_contracts.py) — and the similarity
wrapper passes the (possibly patched) callable through to the body.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore, tenant_scope
from corpus_kb.coding.pooling_math import cosine_similarity
from corpus_kb.coding.pooling_similarity import run_similarity_pooling as _run_body

__all__ = [
    "cosine_similarity",
    "materialize_chunk_keyword_hits",
    "reconcile_coverage",
    "run_similarity_pooling",
]


async def materialize_chunk_keyword_hits(
    pool: CodingStore,
    tenant_id: UUID,
    codebook_version_id: UUID,
) -> dict[str, Any]:
    """Materialize chunk_keyword_hits by word-boundary keyword match.

    For each code, find chunks where inclusion/exclusion keywords appear
    as whole words (word-boundary match), write to chunk_keyword_hits table.

    Args:
        pool: asyncpg pool or SqlGateway store
        tenant_id: Tenant UUID
        codebook_version_id: Codebook version UUID

    Returns:
        Summary dict with counts of keywords matched, chunks touched, etc.
    """
    async with tenant_scope(pool, tenant_id) as conn:
        # Get all keywords per code
        keywords_per_code = await conn.fetch(
            """
            SELECT code_id, keyword, kind FROM code_keywords
            WHERE tenant_id = $1 AND status = 'active'
            """,
            tenant_id,
        )

        hit_count = 0

        for code_row in keywords_per_code:
            code_id = code_row["code_id"]
            keyword = code_row["keyword"]
            kind = code_row["kind"]

            # Word-boundary regex: whole-word match, case-insensitive
            pattern = r"\b" + re.escape(keyword) + r"\b"

            # Find chunks containing this keyword
            chunks = await conn.fetch("SELECT chunk_id FROM chunks WHERE tenant_id = $1", tenant_id)

            for chunk in chunks:
                chunk_id = chunk["chunk_id"]
                # Get chunk text for matching
                chunk_text = await conn.fetchval(
                    "SELECT text FROM chunks WHERE chunk_id = $1 AND tenant_id = $2",
                    chunk_id,
                    tenant_id,
                )

                if chunk_text and re.search(pattern, chunk_text, re.IGNORECASE):
                    # Count hits
                    n_hits = len(re.findall(pattern, chunk_text, re.IGNORECASE))

                    # Write to chunk_keyword_hits
                    await conn.execute(
                        """
                        INSERT INTO chunk_keyword_hits
                          (chunk_id, keyword, code_id, tenant_id, kind, n_hits)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT DO NOTHING
                        """,
                        chunk_id,
                        keyword,
                        code_id,
                        tenant_id,
                        kind,
                        n_hits,
                    )
                    hit_count += 1

    return {"keyword_hits_written": hit_count, "status": "success"}


async def run_similarity_pooling(
    pool: CodingStore,
    tenant_id: UUID,
    codebook_version_id: UUID,
) -> dict[str, Any]:
    """Run NxK probe-similarity pooling against code_registry.probe_vector.

    The body lives in :mod:`pooling_similarity`; the tenant scope resolves
    HERE (module global, so the tests' ``pooling.tenant_scope`` patch is
    the callable the body actually uses) and is injected as a parameter.
    """
    return await _run_body(pool, tenant_id, codebook_version_id, scope=tenant_scope)


async def reconcile_coverage(
    pool: CodingStore,
    tenant_id: UUID,
) -> dict[str, Any]:
    """Reconcile coverage identity: (pooled + never_pooled = corpus_count).

    Mark all chunks as either in_any_pool or never_pooled based on
    whether they appear in chunk_signals.

    No production code path inserts into chunk_status when a chunk is
    ingested (only test fixtures hand-seed it), so this function is
    self-healing: it backfills any missing chunk_status row for this
    tenant's chunks before computing coverage, rather than trusting ingest to
    have done it. It also recomputes in_any_pool from scratch each run
    (true iff a chunk_signals row currently exists, false otherwise) instead
    of only ever setting it true, so a chunk that drops out of every pool
    (a narrowed codebook, a raised pool_floor) is correctly un-flagged on the
    next reconcile rather than staying permanently marked as pooled.

    Args:
        pool: asyncpg pool or SqlGateway store
        tenant_id: Tenant UUID

    Returns:
        Summary with total corpus chunks, pooled, and never-pooled counts
    """
    from corpus_kb.coding._scope import is_gateway

    async with tenant_scope(pool, tenant_id) as conn:
        # Backfill chunk_status for any chunk that was never explicitly
        # seeded into it (the normal case against a real ingested corpus).
        await conn.execute(
            """
            INSERT INTO chunk_status (chunk_id, tenant_id, doc_id)
            SELECT chunk_id, tenant_id, doc_id FROM chunks
            WHERE tenant_id = $1
            ON CONFLICT (chunk_id, tenant_id) DO NOTHING
            """,
            tenant_id,
        )

        # Recompute in_any_pool from scratch: true iff this chunk currently
        # has at least one chunk_signals row, false otherwise. This resets
        # chunks that fell out of every pool, not just marks new pooled ones.
        # The gateway arm ignores parked rows (score NULL — see
        # run_similarity_pooling); Postgres counts every row as shipped.
        parked = "AND csig.score IS NOT NULL" if is_gateway(pool) else ""
        await conn.execute(
            f"""
            UPDATE chunk_status cs
            SET in_any_pool = EXISTS (
                SELECT 1 FROM chunk_signals csig
                WHERE csig.chunk_id = cs.chunk_id AND csig.tenant_id = cs.tenant_id
                {parked}
            )
            WHERE cs.tenant_id = $1
            """,
            tenant_id,
        )

        # Get counts
        total = await conn.fetchval("SELECT COUNT(*) FROM chunks WHERE tenant_id = $1", tenant_id)
        pooled = await conn.fetchval(
            "SELECT COUNT(*) FROM chunk_status WHERE tenant_id = $1 AND in_any_pool = true",
            tenant_id,
        )
        total = total or 0
        never_pooled = total - (pooled or 0)

    return {
        "total_corpus_chunks": total,
        "pooled_chunks": pooled or 0,
        "never_pooled_chunks": never_pooled,
        "coverage_reconciled": True,
    }
