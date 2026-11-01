"""Postgres reads and writes for the labeling layer (migration 028).

Tenant-scoped through ``storage.tenant_conn.tenant_connection`` — the
tables are RLS FORCED, so the tenant GUC lives inside the SAME
transaction as every statement (the shadow-writer house contract). A
build REPLACES the tenant's rows wholesale: ``label_functions`` mirrors
the LF file (input, re-registered per build), ``chunk_labels`` carries
the aggregated label per chunk (derived, rebuildable). One transaction
covers both deletes and both inserts, so a crashed build leaves the
previous build intact.

Stored LF ``config`` jsonb shape: ``{"label": <class>, ...kind config}``
— the class vote rides inside config because the table has no label
column. ``read_lfs`` splits it back out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import asyncpg

from corpus_kb.storage.store_utils import iso_or_none, parse_jsonb
from corpus_kb.storage.tenant_conn import tenant_connection

from .lfs import LFSpec

__all__ = ["labeling_tables_present", "read_labels", "read_lfs", "write_build"]


@dataclass(frozen=True)
class LabelRow:
    """One aggregated chunk label ready for storage."""

    chunk_id: str
    label: str
    probability: float
    detail: dict[str, object]


async def labeling_tables_present(pg_pool: asyncpg.Pool) -> bool:
    """True when migration 028 has run (``to_regclass`` probe, no GUC).

    Migration 028 creates both tables in one file, so one probe covers
    the family (the topics writer's convention).
    """
    async with pg_pool.acquire() as conn:
        return await conn.fetchval("SELECT to_regclass('public.chunk_labels')") is not None


async def write_build(
    pg_pool: asyncpg.Pool,
    tenant_id: str,
    spec: LFSpec,
    label_rows: list[LabelRow],
) -> dict[str, int]:
    """Replace the tenant's LF registry and labels with this build."""
    lf_params = [
        (tenant_id, lf.name, lf.kind, json.dumps({"label": lf.label, **dict(lf.config)}))
        for lf in spec.lfs
    ]
    label_params = [
        (
            tenant_id,
            row.chunk_id,
            row.label,
            min(max(row.probability, 0.0), 1.0),
            json.dumps(row.detail),
        )
        for row in label_rows
    ]
    async with tenant_connection(pg_pool, tenant_id) as conn:
        await conn.execute("DELETE FROM chunk_labels WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM label_functions WHERE tenant_id = $1", tenant_id)
        if lf_params:
            await conn.executemany(
                """
                INSERT INTO label_functions (tenant_id, name, kind, config)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                lf_params,
            )
        if label_params:
            await conn.executemany(
                """
                INSERT INTO chunk_labels (tenant_id, chunk_id, label, probability, detail)
                VALUES ($1, $2::uuid, $3, $4, $5::jsonb)
                """,
                label_params,
            )
    return {"lfs_written": len(lf_params), "labels_written": len(label_params)}


async def read_labels(
    pg_pool: asyncpg.Pool, tenant_id: str, limit: int = 50
) -> list[dict[str, object]]:
    """The tenant's built chunk labels, chunk-id order (bounded window)."""
    async with tenant_connection(pg_pool, tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id::text AS chunk_id, label, probability, created_at
            FROM chunk_labels
            WHERE tenant_id = $1
            ORDER BY chunk_id
            LIMIT $2
            """,
            tenant_id,
            int(limit),
        )
    return [
        {
            "chunk_id": str(row["chunk_id"]),
            "label": row["label"],
            "probability": float(row["probability"]),
            "created_at": iso_or_none(row["created_at"]),
        }
        for row in rows
    ]


async def read_lfs(
    pg_pool: asyncpg.Pool, tenant_id: str, limit: int = 50
) -> list[dict[str, object]]:
    """The tenant's registered LFs, name order (bounded window)."""
    async with tenant_connection(pg_pool, tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT lf_id::text AS lf_id, name, kind, config, created_at
            FROM label_functions
            WHERE tenant_id = $1
            ORDER BY name
            LIMIT $2
            """,
            tenant_id,
            int(limit),
        )
    out: list[dict[str, object]] = []
    for row in rows:
        config = parse_jsonb(row["config"])
        label = config.pop("label", None) if isinstance(config, dict) else None
        out.append(
            {
                "lf_id": str(row["lf_id"]),
                "name": row["name"],
                "kind": row["kind"],
                "label": label,
                "config": config if isinstance(config, dict) else {},
                "created_at": iso_or_none(row["created_at"]),
            }
        )
    return out
