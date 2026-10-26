"""Lifecycle read tools (UI SPA program Task 5b, spec B2).

ONE read-only handler per lifecycle listing the Review / Codebook screens
consume, shared by every surface (CLI command, MCP tool, HTTP GET route,
socket arm) through the usual registry rows:

  * :func:`projects_list`       research_projects, newest first.
  * :func:`codebooks_list`      codebook_versions, newest first. A codebook
    IS an immutable, content-hashed version row in this schema.
  * :func:`codebook_proposals`  research_proposed_codes for the promote
    gate, newest first, one status filter.
  * :func:`coding_assignments`  research_assignments for the review queue,
    newest first, with ``review_status`` / ``code`` filters and offset
    pagination.

House rules carried over from the ingest front door reads
(``ingest_surface_tools``): every read runs inside a tenant-scoped gateway
transaction (the Postgres tenant GUC is set INSIDE the same transaction,
which ``FORCE RLS`` requires), the backend picks its own statement variant
(``$1::uuid`` casts on Postgres, plain varchar predicates on DuckDB), and
a pre-migration database answers ``status: "unavailable"`` with the setup
remedy instead of crashing. Ordering is deterministic with unique-column
tiebreakers, so harnesses can snapshot every list.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import asyncpg

from corpus_kb.storage.gateway import Row, SqlGateway
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID
from corpus_kb.tools.ingest_store import as_gateway, gateway_backend

__all__ = [
    "codebook_proposals",
    "codebooks_list",
    "coding_assignments",
    "projects_list",
]

# Listing window per call (recency page, never the whole table).
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500

_TABLES: dict[str, str] = {
    "projects": "research_projects",
    "codebooks": "codebook_versions",
    "proposals": "research_proposed_codes",
    "assignments": "research_assignments",
}

# The tiebreaker column is always the table's unique key, so equal
# timestamps cannot permute rows.
_ORDER: dict[str, str] = {
    "projects": "ORDER BY created_at DESC, project_id LIMIT {limit} OFFSET {offset}",
    "codebooks": "ORDER BY created_at DESC, version_id LIMIT {limit} OFFSET {offset}",
    "proposals": "ORDER BY created_at DESC, proposed_id LIMIT {limit} OFFSET {offset}",
    "assignments": "ORDER BY assignment_id DESC LIMIT {limit} OFFSET {offset}",
}


def _iso(value: object) -> str | None:
    """One timestamp cell -> ISO text (None passes through)."""
    return value.isoformat() if isinstance(value, datetime) else None


def _uuid(value: object) -> str | None:
    """One UUID cell -> text (None passes through)."""
    return str(value) if value is not None else None


def _page_size(limit: int) -> int:
    """Clamp the page size into the honest 1..MAX window."""
    return max(1, min(int(limit), MAX_LIST_LIMIT))


def _select(kind: str, is_duckdb: bool, columns: str, where: list[str], args: list[object]) -> str:
    """One page select: tenant predicate first, dynamic predicates after.

    ``where`` carries the already-numbered dynamic predicates; the caller
    appends the limit/offset values last, so their placeholders are the
    two highest parameter numbers.
    """
    cast = "" if is_duckdb else "::uuid"
    predicates = [f"tenant_id = $1{cast}", *where]
    order = _ORDER[kind].format(limit=f"${len(args) - 1}", offset=f"${len(args)}")
    return f"SELECT {columns} FROM {_TABLES[kind]} WHERE " + " AND ".join(predicates) + " " + order


async def _fetch_page(
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    kind: str,
    select: str,
    args: list[object],
) -> list[Row] | dict[str, object]:
    """Probe the table, run the page select, share the unavailable envelope.

    Returns the row list on success, or the unavailable envelope when the
    migration has not run on this database.
    """
    gw = as_gateway(pg_pool)
    table = _TABLES[kind]
    if gateway_backend(gw) == "duckdb":
        probe = "SELECT count(*) FROM duckdb_tables() WHERE table_name = $1"
        probe_args: tuple[object, ...] = (table,)
    else:
        probe = "SELECT to_regclass($1)"
        probe_args = (f"public.{table}",)
    async with gw.transaction(tenant_id) as tx:
        if await tx.fetchval(probe, *probe_args) in (None, 0):
            return {
                "status": "unavailable",
                "reason": f"{table} table absent (run corpus-kb setup)",
                "remedy": "run corpus-kb setup",
                "result": [],
            }
        return await tx.fetch(select, *args)


def _envelope(rows: list[Row], render: Callable[[Row], dict[str, object]]) -> dict[str, object]:
    """The house listing envelope: {status, result} with JSON-safe rows."""
    return {"status": "success", "result": [render(row) for row in rows]}


async def projects_list(
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """List research projects, newest first; read-only projection."""
    args: list[object] = [tenant_id, _page_size(limit), 0]
    select = _select(
        "projects",
        gateway_backend(as_gateway(pg_pool)) == "duckdb",
        "project_id, name, created_at",
        [],
        args,
    )
    rows = await _fetch_page(pg_pool, tenant_id, "projects", select, args)
    if isinstance(rows, dict):
        return rows
    return _envelope(
        rows,
        lambda row: {
            "project_id": _uuid(row["project_id"]),
            "name": row["name"],
            "created_at": _iso(row["created_at"]),
        },
    )


async def codebooks_list(
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """List codebook versions (the immutable codebooks), newest first."""
    args: list[object] = [tenant_id, _page_size(limit), 0]
    select = _select(
        "codebooks",
        gateway_backend(as_gateway(pg_pool)) == "duckdb",
        "version_id, label, sha256, paradigm, created_at",
        [],
        args,
    )
    rows = await _fetch_page(pg_pool, tenant_id, "codebooks", select, args)
    if isinstance(rows, dict):
        return rows
    return _envelope(
        rows,
        lambda row: {
            "version_id": _uuid(row["version_id"]),
            "label": row["label"],
            "sha256": row["sha256"],
            "paradigm": row["paradigm"],
            "created_at": _iso(row["created_at"]),
        },
    )


async def codebook_proposals(
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
    status: str = "proposed",
) -> dict[str, object]:
    """List proposed codes for the promote gate, newest first.

    ``status`` accepts the migration's own states: proposed, promoted,
    rejected, duplicate_blocked.
    """
    args: list[object] = [tenant_id, str(status), _page_size(limit), 0]
    select = _select(
        "proposals",
        gateway_backend(as_gateway(pg_pool)) == "duckdb",
        "proposed_id, cluster_id, run_id, suggested_label, n_members, status, "
        "block_reason, created_at",
        ["status = $2"],
        args,
    )
    rows = await _fetch_page(pg_pool, tenant_id, "proposals", select, args)
    if isinstance(rows, dict):
        return rows
    return _envelope(
        rows,
        lambda row: {
            "proposed_id": int(row["proposed_id"]),
            "cluster_id": row["cluster_id"],
            "run_id": _uuid(row["run_id"]),
            "suggested_label": row["suggested_label"],
            "n_members": int(row["n_members"]),
            "status": row["status"],
            "block_reason": row["block_reason"],
            "created_at": _iso(row["created_at"]),
        },
    )


async def coding_assignments(
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
    review_status: str | None = None,
    code: str | None = None,
) -> dict[str, object]:
    """List coding assignments for the review queue, newest first.

    Filters: ``review_status`` (the row status: auto, review, confirmed,
    overridden) and ``code`` (the code id). ``offset`` pages the window.
    """
    where: list[str] = []
    args: list[object] = [tenant_id]
    if review_status is not None:
        args.append(str(review_status))
        where.append(f"status = ${len(args)}")
    if code is not None:
        args.append(str(code))
        where.append(f"code_id = ${len(args)}")
    args.extend([_page_size(limit), max(0, int(offset))])
    select = _select(
        "assignments",
        gateway_backend(as_gateway(pg_pool)) == "duckdb",
        "assignment_id, assignment_aggregate_id, unit_id, code_id, cb_version_id, "
        "run_id, status, confidence, stance, evidence_basis, sim_answer, sim_qa, sim_q, "
        "created_at",
        where,
        args,
    )
    rows = await _fetch_page(pg_pool, tenant_id, "assignments", select, args)
    if isinstance(rows, dict):
        return rows
    return _envelope(
        rows,
        lambda row: {
            "assignment_id": int(row["assignment_id"]),
            "assignment_aggregate_id": _uuid(row["assignment_aggregate_id"]),
            "unit_id": int(row["unit_id"]),
            "code_id": row["code_id"],
            "cb_version_id": _uuid(row["cb_version_id"]),
            "run_id": _uuid(row["run_id"]),
            "status": row["status"],
            "confidence": row["confidence"],
            "stance": row["stance"],
            "evidence_basis": row["evidence_basis"],
            "sim_answer": None if row["sim_answer"] is None else float(row["sim_answer"]),
            "sim_qa": None if row["sim_qa"] is None else float(row["sim_qa"]),
            "sim_q": None if row["sim_q"] is None else float(row["sim_q"]),
            "created_at": _iso(row["created_at"]),
        },
    )
