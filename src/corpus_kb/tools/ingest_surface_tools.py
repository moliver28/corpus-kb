"""MCP ingest-status / ingest-reembed tools (Stage 5 front door).

One handler per operation, shared by every surface (MCP name, CLI
``corpus-kb ingest-status`` / ``ingest-reembed``, HTTP
``GET /api/ingest/status`` / ``POST /api/ingest/reembed``):

  * :func:`ingest_status` reads the ``ingest_runs`` ledger (migration 022)
    plus the degraded-chunk counts, through a ``gateway.transaction`` scope —
    on Postgres the tenant GUC is set INSIDE the same transaction as the
    reads (``FORCE RLS`` makes GUC-less reads return zero rows silently);
    on DuckDB tenancy rides on the explicit ``tenant_id`` predicate. The
    table probe is guarded: a pre-migration database reports
    ``status: "unavailable"`` instead of crashing.
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

from ..storage.gateway import Row, SqlGateway
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass, reembed_degraded
from .ingest_store import as_gateway, gateway_backend

__all__ = ["ingest_reembed", "ingest_status"]

# Runs listed per status call (honest recency window, not the whole ledger).
DEFAULT_RUN_LIMIT = 20

_RUNS_SELECT = """
    SELECT run_id, source_type, started_at, finished_at, status,
           start_hash, finish_hash, stages, error{project_select}
    FROM ingest_runs
    WHERE {tenant_pred}{project_pred}
    ORDER BY {order}
    LIMIT $2
"""


def _parse_stages(raw: object) -> object:
    """The drivers hand JSON back as a string; parse it, keep raw on failure."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _serialize_run(row: Row) -> dict[str, object]:
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
        "project_id": str(row["project_id"]) if row["project_id"] is not None else None,
    }


def _status_sql(is_duckdb: bool) -> dict[str, str]:
    """The backend statement variants for the status read.

    Postgres keeps the pre-port SQL byte-identical (``to_regclass`` probe,
    ``$1::uuid`` casts, the ``information_schema`` degraded probe). DuckDB
    probes ``duckdb_tables``/``duckdb_columns`` and takes plain varchar
    comparisons; ``run_id DESC`` is the deterministic tiebreaker.

    ``project_present`` gates the ``project_id`` column (migration 036): a
    pre-migration ledger reads NULL into every run and the filter answers
    with the honest error, never a SQL crash. The filtered statement binds
    ``$3`` as the project id.
    """
    if is_duckdb:
        return {
            "ledger": "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'ingest_runs'",
            "project_present": (
                "SELECT count(*) FROM duckdb_columns() WHERE table_name = 'ingest_runs' "
                "AND column_name = 'project_id'"
            ),
            "runs": _RUNS_SELECT.format(
                tenant_pred="tenant_id = $1",
                order="started_at DESC, run_id DESC",
                project_select=", NULL AS project_id",
                project_pred="",
            ),
            "runs_project": _RUNS_SELECT.format(
                tenant_pred="tenant_id = $1",
                order="started_at DESC, run_id DESC",
                project_select=", project_id",
                project_pred=" AND project_id = $3",
            ),
            "incomplete": (
                "SELECT count(*) FROM ingest_runs WHERE tenant_id = $1 AND status = 'running'"
            ),
            "degraded_present": (
                "SELECT count(*) FROM duckdb_columns() WHERE table_name IN "
                "('chunks', 'chunks_vectors') AND column_name = 'degraded'"
            ),
            "degraded_chunks": (
                "SELECT count(*) FROM chunks WHERE tenant_id = $1 AND degraded = TRUE"
            ),
            "degraded_vectors": (
                "SELECT count(*) FROM chunks_vectors WHERE tenant_id = $1 AND degraded = TRUE"
            ),
        }
    return {
        "ledger": "SELECT to_regclass('public.ingest_runs')",
        "project_present": (
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'ingest_runs' "
            "AND column_name = 'project_id'"
        ),
        "runs": _RUNS_SELECT.format(
            tenant_pred="tenant_id = $1::uuid",
            order="started_at DESC",
            project_select=", NULL AS project_id",
            project_pred="",
        ),
        "runs_project": _RUNS_SELECT.format(
            tenant_pred="tenant_id = $1::uuid",
            order="started_at DESC",
            project_select=", project_id",
            project_pred=" AND project_id = $3::uuid",
        ),
        "incomplete": (
            "SELECT count(*) FROM ingest_runs WHERE tenant_id = $1::uuid AND status = 'running'"
        ),
        "degraded_present": (
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ANY($1::text[]) "
            "AND column_name = 'degraded'"
        ),
        "degraded_chunks": (
            "SELECT count(*) FROM chunks WHERE tenant_id = $1::uuid AND degraded = TRUE"
        ),
        "degraded_vectors": (
            "SELECT count(*) FROM chunks_vectors WHERE tenant_id = $1::uuid AND degraded = TRUE"
        ),
    }


async def ingest_status(
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_RUN_LIMIT,
    project_id: str | None = None,
) -> dict[str, object]:
    """Recent ``ingest_runs`` entries + degraded counts for one tenant.

    Returns ``{status, ledger_present, runs, incomplete_runs, degraded}``
    where ``degraded`` carries the chunks/chunks_vectors flag counts.
    ``status`` is ``"success"`` when the ledger is readable, else
    ``"unavailable"`` (migration 022 not applied) with the setup remedy.

    ``project_id`` narrows the runs to one project (migration 036). An
    unparsable id, or a pre-migration ledger without the column, is a
    structured ``{"status": "error"}`` answer, never a crash.
    """
    if project_id is not None:
        try:
            UUID(project_id)
        except ValueError:
            return {"status": "error", "message": f"project_id is not a UUID: {project_id}"}
    sql = _status_sql(gateway_backend(as_gateway(pg_pool)) == "duckdb")
    bounded_limit = max(1, min(int(limit), 200))
    async with as_gateway(pg_pool).transaction(tenant_id) as tx:
        if await tx.fetchval(sql["ledger"]) is None:
            return {
                "status": "unavailable",
                "reason": "ingest_runs table absent (migration 022 not applied)",
                "remedy": "run corpus-kb setup",
                "ledger_present": False,
                "runs": [],
                "incomplete_runs": 0,
                "degraded": {"chunks": 0, "chunk_vectors": 0},
            }
        project_col = bool(await tx.fetchval(sql["project_present"]))
        if project_id is not None and not project_col:
            return {
                "status": "error",
                "message": "ingest_runs has no project_id column (migration 036 not applied)",
                "remedy": "run corpus-kb setup",
            }
        filter_project = project_id is not None and project_col
        rows = await tx.fetch(
            sql["runs_project"] if filter_project else sql["runs"],
            tenant_id,
            bounded_limit,
            *(project_id,) if filter_project else (),
        )
        incomplete = await tx.fetchval(sql["incomplete"], tenant_id)
        degraded_present = await tx.fetchval(sql["degraded_present"], ["chunks", "chunks_vectors"])
        degraded = {"chunks": 0, "chunk_vectors": 0}
        if degraded_present == 2:
            degraded["chunks"] = int(await tx.fetchval(sql["degraded_chunks"], tenant_id) or 0)
            degraded["chunk_vectors"] = int(
                await tx.fetchval(sql["degraded_vectors"], tenant_id) or 0
            )
    return {
        "status": "success",
        "ledger_present": True,
        "runs": [_serialize_run(row) for row in rows],
        "incomplete_runs": int(incomplete or 0),
        "degraded": degraded,
    }


async def ingest_reembed(
    pg_pool: asyncpg.Pool | SqlGateway,
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
