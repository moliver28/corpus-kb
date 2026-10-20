"""Postgres reads and writes for the classification layer (migration 029).

Tenant-scoped through ``storage.tenant_conn.tenant_connection`` — both
tables are RLS FORCED, so the tenant GUC lives inside the SAME
transaction as every statement (the shadow-writer house contract).

A build is a derived artifact: the write inserts the ``label_sets`` row
(seed sets are INPUT, so sets survive rebuilds) and REPLACES the tenant's
``chunk_predictions`` rows for that set in one transaction — DELETE by
set, then one batch INSERT (the topics-writer convention).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import asyncpg

from corpus_kb.storage.tenant_conn import tenant_connection

__all__ = ["PredictionRow", "classification_tables_present", "read_predictions", "write_build"]


@dataclass(frozen=True)
class PredictionRow:
    """One per-chunk prediction ready for storage."""

    chunk_id: str
    label: str
    probability: float
    calibrated_probability: float
    review: bool


async def classification_tables_present(pg_pool: asyncpg.Pool) -> bool:
    """True when migration 029 has run (``to_regclass`` probes, no GUC)."""
    async with pg_pool.acquire() as conn:
        label_sets = await conn.fetchval("SELECT to_regclass('public.label_sets')")
        predictions = await conn.fetchval("SELECT to_regclass('public.chunk_predictions')")
    return label_sets is not None and predictions is not None


async def write_build(
    pg_pool: asyncpg.Pool,
    tenant_id: str,
    name: str,
    classes: list[str],
    tier: str,
    params: dict[str, object],
    rows: list[PredictionRow],
) -> dict[str, object]:
    """One transaction: the label_sets row, then wholesale predictions.

    Rows are inserted in chunk-id order, so a harness can snapshot the
    stored build. UUIDs cross the boundary as text (the F13 rule).
    """
    ordered = sorted(rows, key=lambda row: row.chunk_id)
    params_json = json.dumps(params)
    classes_json = json.dumps(classes)
    async with tenant_connection(pg_pool, tenant_id) as conn:
        set_id = await conn.fetchval(
            """
            INSERT INTO label_sets (tenant_id, name, classes)
            VALUES ($1, $2, $3::jsonb)
            RETURNING set_id::text
            """,
            tenant_id,
            name,
            classes_json,
        )
        await conn.execute(
            "DELETE FROM chunk_predictions WHERE tenant_id = $1 AND set_id = $2::uuid",
            tenant_id,
            str(set_id),
        )
        await conn.executemany(
            """
            INSERT INTO chunk_predictions (
                tenant_id, chunk_id, set_id, label, probability,
                calibrated_probability, tier, review, params
            )
            VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9::jsonb)
            """,
            [
                (
                    tenant_id,
                    row.chunk_id,
                    str(set_id),
                    row.label,
                    row.probability,
                    row.calibrated_probability,
                    tier,
                    row.review,
                    params_json,
                )
                for row in ordered
            ],
        )
    return {"set_id": str(set_id), "predictions_written": len(ordered)}


async def read_predictions(
    pg_pool: asyncpg.Pool, tenant_id: str, limit: int
) -> list[dict[str, object]]:
    """Bounded prediction window, ordered by set id then chunk id."""
    async with tenant_connection(pg_pool, tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id::text AS chunk_id, set_id::text AS set_id, label,
                   probability, calibrated_probability, tier, review
            FROM chunk_predictions
            WHERE tenant_id = $1
            ORDER BY set_id, chunk_id
            LIMIT $2
            """,
            tenant_id,
            int(limit),
        )
    return [
        {
            "chunk_id": str(row["chunk_id"]),
            "set_id": str(row["set_id"]),
            "label": row["label"],
            "probability": float(row["probability"]),
            "calibrated_probability": (
                None
                if row["calibrated_probability"] is None
                else float(row["calibrated_probability"])
            ),
            "tier": row["tier"],
            "review": bool(row["review"]),
        }
        for row in rows
    ]
