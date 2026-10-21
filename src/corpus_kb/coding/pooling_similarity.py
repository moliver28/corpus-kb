"""The similarity-pooling body (pure move out of ``pooling.py``).

``run_similarity_pooling`` is the byte-identical body of
``pooling.run_similarity_pooling`` with ONE deliberate seam: the tenant
scope enters as the ``scope`` parameter, because the pooling module's
tests monkeypatch ``pooling.tenant_scope`` and the wrapper there passes
its module global through — the patched callable is exactly the one used.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore, ScopedConn
from corpus_kb.coding.pooling_math import cosine_similarity


async def run_similarity_pooling(
    pool: CodingStore,
    tenant_id: UUID,
    codebook_version_id: UUID,
    *,
    scope: Callable[[CodingStore, UUID], AbstractAsyncContextManager[ScopedConn]],
) -> dict[str, Any]:
    """Run NxK probe-similarity pooling against code_registry.probe_vector.

    For each code with a valid probe_vector:
    - Find chunks whose definition_vectors have cosine similarity >= pool_floor
    - Write chunk_signals with "vector" signal type
    - Filter by pool_floor threshold

    Args:
        pool: asyncpg pool or SqlGateway store
        tenant_id: Tenant UUID
        codebook_version_id: Codebook version UUID
        scope: the tenant_scope callable (injected by the pooling wrapper
            so the module-global monkeypatch contract keeps working)

    Returns:
        Summary dict with similarity scores computed, chunk_signals written
    """
    from corpus_kb.coding._scope import is_gateway

    async with scope(pool, tenant_id) as conn:
        signals_written = 0
        # The gateway cannot DELETE chunk_signals rows (its append-only
        # guard stands in for the dropped PG trigger), so the demoted-pair
        # path parks the stale row at score NULL through the sanctioned
        # upsert instead; reconcile_coverage reads NULL as "not pooled"
        # below. Postgres keeps the delete exactly as shipped.
        portable = is_gateway(pool)

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
                SELECT c.chunk_id, cv.embedding FROM chunks c
                JOIN chunks_vectors cv ON c.chunk_id = cv.chunk_id
                WHERE c.tenant_id = $1
                """,
                tenant_id,
            )

            for chunk in chunks:
                chunk_id = chunk["chunk_id"]
                chunk_vector = chunk["embedding"]

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
                    if portable:
                        # Gateway arm: park the row at score NULL through the
                        # sanctioned upsert; the guard forbids DELETE here.
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
                            None,
                            "below_pool_floor",
                        )
                        continue
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
