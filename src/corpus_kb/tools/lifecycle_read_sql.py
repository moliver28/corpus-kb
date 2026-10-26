"""Shared read plumbing for the lifecycle read tools (UI SPA Task 5b).

The mechanical half of the lifecycle listings (spec B2): the page window,
the deterministic per-table ORDER BY with unique-column tiebreakers, the
backend-aware SELECT builder, and the tenant-scoped table-probe fetch.
The four public handlers live in ``lifecycle_read_tools``; this module
holds ONLY the parts they share, so a listing change touches one place.

House rules carried over from the ingest front door reads
(``ingest_surface_tools``): every read runs inside a tenant-scoped gateway
transaction (the Postgres tenant GUC is set INSIDE the same transaction,
which ``FORCE RLS`` requires), the backend picks its own statement variant
(``$1::uuid`` casts on Postgres, plain varchar predicates on DuckDB), and
a pre-migration database answers ``status: "unavailable"`` with the setup
remedy instead of crashing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import asyncpg

from corpus_kb.errors import ValidationError
from corpus_kb.storage.gateway import Row, SqlGateway
from corpus_kb.tools.ingest_store import as_gateway, gateway_backend

__all__ = [
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "envelope",
    "fetch_page",
    "iso_text",
    "page_size",
    "select_page",
    "uuid_text",
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


def iso_text(value: object) -> str | None:
    """One timestamp cell -> ISO text (None passes through)."""
    return value.isoformat() if isinstance(value, datetime) else None


def uuid_text(value: object) -> str | None:
    """One UUID cell -> text (None passes through)."""
    return str(value) if value is not None else None


def page_size(limit: int) -> int:
    """Clamp the page size into the honest 1..MAX window.

    Junk is a typed validation error on EVERY surface (GET query value,
    socket JSON, CLI flag): one failure class, never a silent clamp.
    """
    try:
        value = int(limit)
    except (TypeError, ValueError):
        raise ValidationError("limit must be an integer") from None
    return max(1, min(value, MAX_LIST_LIMIT))


def select_page(
    kind: str, is_duckdb: bool, columns: str, where: list[str], args: list[object]
) -> str:
    """One page select: tenant predicate first, dynamic predicates after.

    ``where`` carries the already-numbered dynamic predicates; the caller
    appends the limit/offset values last, so their placeholders are the
    two highest parameter numbers.
    """
    cast = "" if is_duckdb else "::uuid"
    predicates = [f"tenant_id = $1{cast}", *where]
    order = _ORDER[kind].format(limit=f"${len(args) - 1}", offset=f"${len(args)}")
    return f"SELECT {columns} FROM {_TABLES[kind]} WHERE " + " AND ".join(predicates) + " " + order


async def fetch_page(
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


def envelope(rows: list[Row], render: Callable[[Row], dict[str, object]]) -> dict[str, object]:
    """The house listing envelope: {status, result} with JSON-safe rows."""
    return {"status": "success", "result": [render(row) for row in rows]}
