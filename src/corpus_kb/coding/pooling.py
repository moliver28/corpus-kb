"""Pooling module: keyword hits, vector similarity, chunk_signals materialization."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import asyncpg
import numpy as np

from corpus_kb.storage.tenant_conn import tenant_connection


def _as_array(vector: np.ndarray | list[float] | str) -> np.ndarray:
    """Coerce a vector to float32 ndarray.

    asyncpg has no pgvector codec registered here, so a `vector` column comes
    back as its text literal ("[0.1,0.2,...]"); parse that shape too.
    """
    if isinstance(vector, str):
        return np.array(json.loads(vector), dtype=np.float32)
    if isinstance(vector, np.ndarray):
        return vector.astype(np.float32, copy=False)
    return np.array(vector, dtype=np.float32)


def cosine_similarity(
    v1: np.ndarray | list[float] | str, v2: np.ndarray | list[float] | str
) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        v1: First vector (numpy array, list, or pgvector text literal)
        v2: Second vector (numpy array, list, or pgvector text literal)

    Returns:
        Cosine similarity value between -1.0 and 1.0 (higher is more similar)
    """
    v1 = _as_array(v1)
    v2 = _as_array(v2)

    dot = np.dot(v1, v2)
    norm = np.linalg.norm(v1) * np.linalg.norm(v2)
    if norm < 1e-9:
        return 0.0
    return float(np.clip(dot / norm, -1.0, 1.0))


async def materialize_chunk_keyword_hits(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
) -> dict[str, Any]:
    """Materialize chunk_keyword_hits by word-boundary keyword match.

    For each code, find chunks where inclusion/exclusion keywords appear
    as whole words (word-boundary match), write to chunk_keyword_hits table.

    Args:
        pool: asyncpg connection pool
        tenant_id: Tenant UUID
        codebook_version_id: Codebook version UUID

    Returns:
        Summary dict with counts of keywords matched, chunks touched, etc.
    """
    async with tenant_connection(pool, tenant_id) as conn:
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
                    "SELECT text FROM chunks WHERE chunk_id = $1", chunk_id
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
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
) -> dict[str, Any]:
    """Run NxK probe-similarity pooling against code_registry.probe_vector.

    For each code with a valid probe_vector:
    - Find chunks whose definition_vectors have cosine similarity >= pool_floor
    - Write chunk_signals with "vector" signal type
    - Filter by pool_floor threshold

    Args:
        pool: asyncpg connection pool
        tenant_id: Tenant UUID
        codebook_version_id: Codebook version UUID

    Returns:
        Summary dict with similarity scores computed, chunk_signals written
    """
    async with tenant_connection(pool, tenant_id) as conn:
        signals_written = 0

        # Get all codes with probe vectors
        codes = await conn.fetch(
            """
            SELECT code_id, probe_vector, pool_floor FROM code_registry
            WHERE tenant_id = $1 AND codebook_version_id = $2 AND probe_vector IS NOT NULL
            """,
            tenant_id,
            codebook_version_id,
        )

        for code_row in codes:
            code_id = code_row["code_id"]
            probe_vector = code_row["probe_vector"]
            pool_floor = code_row["pool_floor"] or 0.3

            # Get all chunks with vectors
            chunks = await conn.fetch(
                """
                SELECT c.chunk_id, cv.vector FROM chunks c
                JOIN chunks_vectors cv ON c.chunk_id = cv.chunk_id
                WHERE c.tenant_id = $1
                """,
                tenant_id,
            )

            for chunk in chunks:
                chunk_id = chunk["chunk_id"]
                chunk_vector = chunk["vector"]

                # Compute cosine similarity between probe_vector and chunk_vector
                similarity = cosine_similarity(probe_vector, chunk_vector)

                if similarity >= pool_floor:
                    # chunk_signals' natural key is (chunk_id, code_id, signal,
                    # tenant_id): one current row per chunk/code/signal-type.
                    # `detail` (the formatted score string) is mutable payload,
                    # not part of the key, so a shifted similarity (re-embed,
                    # re-ingest, dimension migration) corrects the existing row
                    # in place instead of accumulating a stale duplicate that
                    # coder_dispatch would then have to pick between. See
                    # migrations/014_chunk_signals_upsert.sql.
                    await conn.execute(
                        """
                        INSERT INTO chunk_signals
                          (chunk_id, code_id, tenant_id, signal, score, detail)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (chunk_id, code_id, signal, tenant_id)
                        DO UPDATE SET score = EXCLUDED.score,
                                      detail = EXCLUDED.detail,
                                      created_at = EXCLUDED.created_at
                        """,
                        chunk_id,
                        code_id,
                        tenant_id,
                        "vector",
                        similarity,
                        f"sim={similarity:.4f}",
                    )
                    signals_written += 1
                else:
                    # This pair no longer qualifies (pool_floor raised, or the
                    # re-embedded vector drifted below it). A stale "vector"
                    # row from a prior run must be removed, not left behind,
                    # so reconcile_coverage's in_any_pool recompute (which
                    # keys off "does a chunk_signals row currently exist")
                    # correctly drops this chunk out of the pool rather than
                    # leaving it permanently flagged as pooled.
                    await conn.execute(
                        """
                        DELETE FROM chunk_signals
                        WHERE chunk_id = $1 AND code_id = $2 AND tenant_id = $3 AND signal = $4
                        """,
                        chunk_id,
                        code_id,
                        tenant_id,
                        "vector",
                    )

    return {"chunk_signals_written": signals_written, "status": "success"}


async def reconcile_coverage(
    pool: asyncpg.Pool,
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
        pool: asyncpg connection pool
        tenant_id: Tenant UUID

    Returns:
        Summary with total corpus chunks, pooled, and never-pooled counts
    """
    async with tenant_connection(pool, tenant_id) as conn:
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
        await conn.execute(
            """
            UPDATE chunk_status cs
            SET in_any_pool = EXISTS (
                SELECT 1 FROM chunk_signals csig
                WHERE csig.chunk_id = cs.chunk_id AND csig.tenant_id = cs.tenant_id
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
