"""Calibrate per-code similarity-pooling floors from human-coded examples.

floors.calibrate() is pure statistics over cosine similarities; this module is
the DB-facing wiring around it. There is no labeled-example data available at
codebook-load time (a codebook's `examples` field is illustrative text, not a
set of chunk-level positive/negative decisions with known vectors), so this
runs as its own explicit step: an operator (or an automated gate) submits
chunk_ids already known to be positive/negative for one code -- typically
drawn from accepted/rejected chunk_codes decisions -- and this computes the
cosine similarity of each against that code's probe_vector, then calibrates
and writes pool_floor/residual_floor back onto its code_registry row.

Kept out of codebook_loader.py (a different transaction shape: one code, one
short read-then-write, no embedding calls) and out of pooling.py (this writes
code_registry, pooling only reads it), following the same
"extract to a sibling module when the shape differs" pattern as
codebook_loader.py itself.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.coding.floors import calibrate
from corpus_kb.coding.pooling import cosine_similarity
from corpus_kb.storage.tenant_conn import tenant_connection


async def _fetch_vectors(
    conn: asyncpg.Connection, tenant_id: UUID, chunk_ids: list[UUID]
) -> dict[UUID, Any]:
    if not chunk_ids:
        return {}
    rows = await conn.fetch(
        "SELECT chunk_id, vector FROM chunks_vectors"
        " WHERE tenant_id=$1 AND chunk_id = ANY($2::uuid[])",
        tenant_id,
        chunk_ids,
    )
    return {row["chunk_id"]: row["vector"] for row in rows}


async def calibrate_code_floors(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    code_id: str,
    codebook_version_id: UUID,
    positive_chunk_ids: list[UUID],
    negative_chunk_ids: list[UUID],
    min_pos: int = 10,
    global_fallback: float | None = None,
) -> dict[str, Any]:
    """Calibrate and persist pool_floor/residual_floor for one code.

    Cosines are computed between each labeled chunk's stored vector and the
    code's probe_vector (the same vector run_similarity_pooling pools against),
    so the calibrated floors are on the exact axis they will gate.

    A chunk_id with no row in chunks_vectors is silently skipped (never
    ingested, or ingested without an embedding yet) rather than failing the
    whole calibration: partial labeled data is still usable input to
    floors.calibrate's fallback path.

    Returns a dict with the resolved (pool_floor, residual_floor), the counts
    of positive/negative cosines actually used, and whether the fallback was
    applied (len(pos) < min_pos).

    Raises:
        ValueError: if code_id has no row (no probe_vector) under this
            codebook_version_id/tenant, so there is nothing to write into.
    """
    async with tenant_connection(pool, tenant_id) as conn:
        probe_vector = await conn.fetchval(
            "SELECT probe_vector FROM code_registry "
            "WHERE code_id=$1 AND codebook_version_id=$2 AND tenant_id=$3",
            code_id,
            codebook_version_id,
            tenant_id,
        )
        if probe_vector is None:
            raise ValueError(
                f"code_id {code_id!r} has no probe_vector under codebook_version_id "
                f"{codebook_version_id} for this tenant; load the codebook first"
            )

        pos_vectors = await _fetch_vectors(conn, tenant_id, positive_chunk_ids)
        neg_vectors = await _fetch_vectors(conn, tenant_id, negative_chunk_ids)

        pos_cosines = [cosine_similarity(v, probe_vector) for v in pos_vectors.values()]
        neg_cosines = [cosine_similarity(v, probe_vector) for v in neg_vectors.values()]

        pool_floor, residual_floor = calibrate(
            pos_cosines, neg_cosines, min_pos=min_pos, global_fallback=global_fallback
        )

        await conn.execute(
            "UPDATE code_registry SET pool_floor=$1, residual_floor=$2 "
            "WHERE code_id=$3 AND codebook_version_id=$4 AND tenant_id=$5",
            pool_floor,
            residual_floor,
            code_id,
            codebook_version_id,
            tenant_id,
        )

    return {
        "status": "success",
        "code_id": code_id,
        "pool_floor": pool_floor,
        "residual_floor": residual_floor,
        "n_positive_used": len(pos_cosines),
        "n_negative_used": len(neg_cosines),
        "n_positive_requested": len(positive_chunk_ids),
        "n_negative_requested": len(negative_chunk_ids),
        "fallback_applied": len(pos_cosines) < min_pos,
    }
