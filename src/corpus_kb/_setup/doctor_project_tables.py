"""The doctor's project-tables probe (Track C, zero-drift rule 1).

One read-only check for the storage section: the ``project_tables``
registry is the catalogue of the runtime ``raw_<slug>`` tables, so the
doctor reports orphans (a raw table with no registry row) and gaps (a
registry row whose table is missing). Never raises; an unreadable
database answers an honest WARN.
"""

from __future__ import annotations

from typing import Any

from corpus_kb._setup.doctor_duckdb import DOC_ADMIN
from corpus_kb._setup.doctor_research import (
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARN,
    ResearchCheck,
)
from corpus_kb.utils.config_sections import mapping_section

__all__ = ["project_tables_check"]

_REGISTRY_TABLE = "project_tables"


async def _tables_present(con: Any, is_postgres: bool) -> bool:
    """True when the registry table exists on this database."""
    if is_postgres:
        found = await con.fetchval("SELECT to_regclass('public.project_tables')")
        return found is not None
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = 'project_tables'"
    ).fetchone()
    return bool(row and row[0])


async def _read_state(con: Any, is_postgres: bool) -> tuple[list[str], list[str], list[str]]:
    """(registered, orphans, missing) table-name lists, sorted.

    DuckDB's ``execute`` answers the connection itself, so every read
    fetches rows explicitly; asyncpg's ``fetch`` already returns them.
    """
    if is_postgres:
        registered_rows = await con.fetch(
            f"SELECT table_name FROM {_REGISTRY_TABLE} ORDER BY table_name"
        )
        present_rows = await con.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'raw%' ORDER BY table_name"
        )
    else:
        registered_rows = con.execute(
            f"SELECT table_name FROM {_REGISTRY_TABLE} ORDER BY table_name"
        ).fetchall()
        present_rows = con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'raw%' ORDER BY table_name"
        ).fetchall()
    registered = [str(row[0]) for row in registered_rows]
    present = [str(row[0]) for row in present_rows]
    reg_set = set(registered)
    orphans = [name for name in present if name not in reg_set]
    present_set = set(present)
    missing = [name for name in registered if name not in present_set]
    return registered, orphans, missing


async def project_tables_check(config: dict[str, Any], conn_str: str) -> ResearchCheck:
    """The project raw-table registry check for one backend (never raises)."""
    backend = str(mapping_section(config, "database").get("backend") or "postgres")
    name = "project raw tables"
    try:
        if backend == "duckdb":
            import duckdb

            from corpus_kb.storage.duckdb_support import duckdb_conn_string_to_path

            con = duckdb.connect(duckdb_conn_string_to_path(conn_str), read_only=True)
            try:
                if not await _tables_present(con, is_postgres=False):
                    return ResearchCheck(
                        name=name,
                        status=STATUS_INFO,
                        detail="migration 043 has not run yet, so no project raw tables exist.",
                        fix="Run the migrations, then provision projects.",
                        doc=DOC_ADMIN,
                    )
                registered, orphans, missing = await _read_state(con, is_postgres=False)
            finally:
                con.close()
        else:
            import asyncpg

            from corpus_kb.contracts.model import DEFAULT_TENANT_ID

            con = await asyncpg.connect(conn_str)
            try:
                if not await _tables_present(con, is_postgres=True):
                    return ResearchCheck(
                        name=name,
                        status=STATUS_INFO,
                        detail="migration 043 has not run yet, so no project raw tables exist.",
                        fix="Run the migrations, then provision projects.",
                        doc=DOC_ADMIN,
                    )
                async with con.transaction():
                    # The registry carries tenant RLS with FORCE, so the
                    # probe reads the default tenant's rows like a study does.
                    await con.execute(
                        "SELECT set_config('app.current_tenant_id', $1, true)",
                        DEFAULT_TENANT_ID,
                    )
                    registered, orphans, missing = await _read_state(con, is_postgres=True)
            finally:
                await con.close()
    except Exception as exc:
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=f"could not read the project raw-table registry: {exc}",
            fix="Check the database connection, then run corpus-kb doctor again.",
            doc=DOC_ADMIN,
        )
    problems = orphans + missing
    if problems:
        detail = (
            f"{len(registered)} registered table(s); orphan or missing: "
            + ", ".join(problems)
            + "."
        )
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=detail,
            fix=(
                "Align the registry: re-provision missing tables, or drop "
                "orphan raw tables through project-drop."
            ),
            doc=DOC_ADMIN,
        )
    detail = (
        f"{len(registered)} registered table(s), none orphan or missing."
        if registered
        else "no projects provisioned yet."
    )
    return ResearchCheck(name=name, status=STATUS_OK, detail=detail, fix="", doc=DOC_ADMIN)
