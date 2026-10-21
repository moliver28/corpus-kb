"""Storage-backend section for ``corpus-kb doctor`` (storage portability §9).

Assembles the read-only section the doctor prints: the engine line
(``doctor_engine.py``), the DuckDB health checks (``doctor_duckdb.py``:
file lock, fts watermark, extension probes), and the SQLite event-store
sidecar health. No check here ever flips the doctor exit code.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

from corpus_kb._setup.doctor_duckdb import (
    DOC_ADMIN,
    DOC_INSTALL,
    DUCKDB_SETUP_FIX,
    duckdb_lock_check,
    extension_load_check,
    fts_watermark_check,
)
from corpus_kb._setup.doctor_engine import (
    engine_fit_check,
    pg_chunk_count,
    resolved_backend,
)
from corpus_kb._setup.doctor_research import (
    STATUS_FAIL,
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARN,
    ResearchCheck,
)
from corpus_kb.errors import ConfigError
from corpus_kb.storage.duckdb_support import (
    duckdb_conn_string_to_path,
    events_sidecar_path,
)

if TYPE_CHECKING:  # function-local at runtime (program rule 9)
    import duckdb


def events_sidecar_check(dsn: str) -> ResearchCheck:
    """The SQLite event store: opens, has the schema, event count reachable.

    FAILs when the configured analytics path names the sidecar itself —
    SQLite cannot open a DuckDB file, so that config would clobber the
    event store (the ``events_sidecar_path`` ConfigError hazard).
    """
    name = "SQLite event store"
    try:
        sidecar = events_sidecar_path(dsn)
    except ConfigError as exc:
        return ResearchCheck(
            name=name,
            status=STATUS_FAIL,
            detail=str(exc),
            fix="Point database.connection_string at the analytics .duckdb file.",
            doc=DOC_ADMIN,
        )
    if sidecar == ":memory:":
        return ResearchCheck(
            name=name,
            status=STATUS_INFO,
            detail="the event store is in memory. Events do not persist.",
            doc=DOC_ADMIN,
        )
    if not Path(sidecar).exists():
        return ResearchCheck(
            name=name,
            status=STATUS_INFO,
            detail=f"no event store at {sidecar}. It is created on first write.",
            fix=DUCKDB_SETUP_FIX,
            doc=DOC_INSTALL,
        )
    try:
        with closing(sqlite3.connect(sidecar)) as con:
            rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if "stored_events" not in {str(row[0]) for row in rows}:
                return ResearchCheck(
                    name=name,
                    status=STATUS_WARN,
                    detail="the sidecar file holds no event tables.",
                    fix=DUCKDB_SETUP_FIX,
                    doc=DOC_INSTALL,
                )
            count = int(con.execute("SELECT count(*) FROM stored_events").fetchone()[0])
    except sqlite3.Error as exc:
        return ResearchCheck(
            name=name,
            status=STATUS_FAIL,
            detail=f"cannot open the sidecar: {exc}",
            fix="Move the file away, then run " + DUCKDB_SETUP_FIX,
            doc=DOC_ADMIN,
        )
    return ResearchCheck(
        name=name,
        status=STATUS_OK,
        detail=f"sidecar healthy ({count} events)",
        doc=DOC_ADMIN,
    )


def _duckdb_importable() -> bool:
    import importlib.util

    return importlib.util.find_spec("duckdb") is not None


def _chunk_count(con: duckdb.DuckDBPyConnection) -> int | None:
    """The chunks row count, or None when the migrations never ran."""
    import duckdb

    try:
        row = con.execute("SELECT count(*) FROM chunks").fetchone()
        return int(row[0]) if row else None
    except duckdb.CatalogException:
        return None


async def storage_checks(
    config: dict[str, Any], conn_str: str, postgres_ok: bool
) -> list[ResearchCheck]:
    """Collect the storage section for the doctor report (never raises)."""
    backend = resolved_backend(config)
    if backend not in {"postgres", "duckdb"}:
        return [
            ResearchCheck(
                name="database backend",
                status=STATUS_WARN,
                detail=f"unknown database.backend value {backend!r}.",
                fix="Set database.backend to postgres or duckdb.",
                doc=DOC_ADMIN,
            )
        ]
    if backend != "duckdb":
        count = await pg_chunk_count(conn_str) if postgres_ok and conn_str else None
        return [engine_fit_check("postgres", count)]

    if not conn_str.startswith("duckdb:"):
        fail = ResearchCheck(
            name="database config pair",
            status=STATUS_FAIL,
            detail=(
                "database.backend is duckdb, but the connection string is not a duckdb:/// file."
            ),
            fix=DUCKDB_SETUP_FIX,
            doc=DOC_INSTALL,
        )
        return [fail, engine_fit_check("duckdb", None)]
    if not _duckdb_importable():
        return [
            ResearchCheck(
                name="duckdb python package",
                status=STATUS_INFO,
                detail="the duckdb extra is not installed in this environment.",
                fix='pip install -e ".[duckdb]"',
                doc=DOC_INSTALL,
            ),
            engine_fit_check("duckdb", None),
        ]

    import duckdb

    path = duckdb_conn_string_to_path(conn_str)
    checks: list[ResearchCheck] = [duckdb_lock_check(path)]
    count: int | None = None
    if checks[0].status == STATUS_OK:
        con = duckdb.connect(path, read_only=True)
        try:
            count = _chunk_count(con)
            checks.append(fts_watermark_check(con))
        finally:
            con.close()
    with closing(duckdb.connect(":memory:")) as scratch:
        checks.extend(extension_load_check(scratch))
    checks.append(events_sidecar_check(conn_str))
    checks.append(engine_fit_check("duckdb", count))
    return checks


def print_storage_checks(checks: list[ResearchCheck]) -> None:
    """Print the storage section (ASCII only for cp1252 consoles)."""
    print("\nStorage backend:")
    for check in checks:
        print(f"  {check.name}: {check.status.upper()} - {check.detail}")
        if check.fix:
            print(f"    fix: {check.fix}")
        if check.doc:
            print(f"    docs: {check.doc}")


__all__ = [
    "duckdb_lock_check",
    "engine_fit_check",
    "events_sidecar_check",
    "extension_load_check",
    "fts_watermark_check",
    "print_storage_checks",
    "resolved_backend",
    "storage_checks",
]
