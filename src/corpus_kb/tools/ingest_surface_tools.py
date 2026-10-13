"""MCP ingest-status / ingest-reembed tools (Stage 5 front door).

One handler per operation, shared by every surface (MCP name, CLI
``corpus-kb ingest-status`` / ``ingest-reembed``, HTTP
``GET /api/ingest/status`` / ``POST /api/ingest/reembed``):

  * :func:`ingest_status` reads the ``ingest_runs`` ledger (migration 022)
    plus the degraded-chunk counts, through a tenant-GUC transaction —
    ``FORCE RLS`` makes GUC-less reads return zero rows silently, so the
    GUC is set INSIDE the same transaction as the reads (doctor pattern).
    The table probe is ``to_regclass``-guarded: a pre-migration database
    reports ``status: "unavailable"`` instead of crashing.
  * :func:`ingest_reembed` is the thin wrapper over
    ``tools.ingest_common.reembed_degraded`` (the Stage-3 handler); it adds
    argument validation only.

Lives in its own module (not ``ingest_tools.py``): that file is already
near the 250-line soft limit and this surface set is versioned separately
in the MCP contract snapshot.
"""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass, reembed_degraded

__all__ = ["ingest_reembed", "ingest_status"]

# Runs listed per status call (honest recency window, not the whole ledger).
DEFAULT_RUN_LIMIT = 20


def _parse_stages(raw: object) -> object:
    """asyncpg hands jsonb back as a string; parse it, keep raw on failure."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _serialize_run(row: asyncpg.Record) -> dict[str, object]:
    """One ledger row -> JSON-safe dict (timestamps ISO, stages as parsed)."""
    return {
        "run_id": str(row["run_id"]),
        "source_type": row["source_type"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "status": row["status"],
        "start_hash": row["start_hash"],
        "finish_hash": row["finish_hash"],
        "stages": _parse_stages(row["stages"]),
        "error": row["error"],
    }


async def ingest_status(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_RUN_LIMIT,
) -> dict[str, object]:
    """Recent ``ingest_runs`` entries + degraded counts for one tenant.

    Returns ``{status, ledger_present, runs, incomplete_runs, degraded}``
    where ``degraded`` carries the chunks/chunks_vectors flag counts.
    ``status`` is ``"success"`` when the ledger is readable, else
    ``"unavailable"`` (migration 022 not applied) with the setup remedy.
    """
    del config  # accepted for MCP-tool signature symmetry; status is config-free
    bounded_limit = max(1, min(int(limit), 200))
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
        rel = await conn.fetchval("SELECT to_regclass('public.ingest_runs')")
        if rel is None:
            return {
                "status": "unavailable",
                "reason": "ingest_runs table absent (migration 022 not applied)",
                "remedy": "run corpus-kb setup",
                "ledger_present": False,
                "runs": [],
                "incomplete_runs": 0,
                "degraded": {"chunks": 0, "chunk_vectors": 0},
            }
        rows = await conn.fetch(
            """
            SELECT run_id, source_type, started_at, finished_at, status,
                   start_hash, finish_hash, stages, error
            FROM ingest_runs
            WHERE tenant_id = $1::uuid
            ORDER BY started_at DESC
            LIMIT $2
            """,
            tenant_id,
            bounded_limit,
        )
        incomplete = await conn.fetchval(
            "SELECT count(*) FROM ingest_runs WHERE tenant_id = $1::uuid AND status = 'running'",
            tenant_id,
        )
        degraded_present = await conn.fetchval(
            """
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY($1::text[])
              AND column_name = 'degraded'
            """,
            ["chunks", "chunks_vectors"],
        )
        degraded = {"chunks": 0, "chunk_vectors": 0}
        if degraded_present == 2:
            degraded["chunks"] = int(
                await conn.fetchval(
                    "SELECT count(*) FROM chunks WHERE tenant_id = $1::uuid AND degraded = TRUE",
                    tenant_id,
                )
            )
            degraded["chunk_vectors"] = int(
                await conn.fetchval(
                    """
                    SELECT count(*) FROM chunks_vectors
                    WHERE tenant_id = $1::uuid AND degraded = TRUE
                    """,
                    tenant_id,
                )
            )
    return {
        "status": "success",
        "ledger_present": True,
        "runs": [_serialize_run(row) for row in rows],
        "incomplete_runs": int(incomplete or 0),
        "degraded": degraded,
    }


async def ingest_reembed(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int | None = None,
    doc_id: str | None = None,
) -> dict[str, object]:
    """Re-embed degraded (zero-vector) chunks; honest scanned/fixed/failed.

    Thin surface wrapper over :func:`reembed_degraded` (argument validation
    only): an unparsable ``doc_id`` is a structured error, never a crash.
    """
    config = load_config_or_pass(config)
    if doc_id is not None:
        try:
            UUID(doc_id)
        except ValueError:
            return {"status": "error", "message": f"doc_id is not a UUID: {doc_id}"}
    if limit is not None and limit < 1:
        return {"status": "error", "message": f"limit must be >= 1, got {limit}"}
    return await reembed_degraded(pg_pool, config, tenant_id, limit=limit, doc_id=doc_id)
