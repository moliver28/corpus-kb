"""The store-version boot guard (W5-a).

One contract, three serve paths (HTTP startup, DuckDB startup, stdio):
the server refuses to serve when the STORE schema is NEWER than the
CODE. The refusal is the typed :class:`~corpus_kb.errors.StoreNewerError`
with the exact files the code lacks, so no partial wiring happens.

An OLDER store proceeds unchanged — migrations stay a ``setup``/``update``
step, exactly as before this guard existed. A FRESH store (no tracking
table) proceeds too.

The comparison is number-based: a store migration row whose numeric
prefix exceeds the highest packaged forward migration is "ahead".
Rollback files never count. The pure core (:func:`check_store_not_newer`)
carries the contract; the readers carry one probe per engine; the serve
paths call :func:`assert_store_supported` before they wire anything.
"""

from __future__ import annotations

import asyncio
import importlib.resources as resources
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from corpus_kb._setup.migrate import DUCKDB_DSN_PREFIX
from corpus_kb.errors import StoreNewerError


@dataclass(frozen=True)
class StoreVersionReport:
    """What one guard comparison saw.

    ``pending_count`` counts packaged forward migrations the store has
    not recorded yet; ``fresh`` marks a store with no applied rows.
    """

    store_head: str | None
    code_head: str | None
    pending_count: int
    fresh: bool


def migration_number(name: str) -> int:
    """The numeric prefix of a numbered migration filename; junk is 0."""
    head = name.split("_", 1)[0]
    return int(head) if head.isdigit() else 0


def code_migration_names() -> list[str]:
    """The packaged forward migration filenames, sorted."""
    ref = resources.files("corpus_kb") / "migrations"
    with resources.as_file(ref) as path:
        return sorted(
            p.name
            for p in path.iterdir()
            if p.is_file() and p.suffix == ".sql" and p.name[0].isdigit()
        )


def check_store_not_newer(applied: Iterable[str], code: Iterable[str]) -> StoreVersionReport:
    """Compare the applied set with the code set; refuse a newer store.

    Raises :class:`StoreNewerError` naming every store migration the
    code does not know, plus the exact remediation. Anything else —
    equal, older, fresh — answers the report.
    """
    applied_names = sorted(set(applied))
    code_names = sorted(set(code))
    code_head = code_names[-1] if code_names else None
    store_head = applied_names[-1] if applied_names else None
    code_max = migration_number(code_head) if code_head else 0
    ahead = [name for name in applied_names if migration_number(name) > code_max]
    if ahead:
        head_name = code_head or "no migration"
        raise StoreNewerError(
            f"The store has migrations this code does not know: {', '.join(ahead)}. "
            f"This code ends at {head_name}."
        )
    applied_set = set(applied_names)
    pending = [name for name in code_names if name not in applied_set]
    return StoreVersionReport(
        store_head=store_head,
        code_head=code_head,
        pending_count=len(pending),
        fresh=not applied_names,
    )


async def read_applied_pg(conn_str: str) -> list[str] | None:
    """The applied Postgres migration names, or None on a fresh store.

    Connect failures escape (an unreachable database is not a fresh
    store); a missing ``corpus.schema_migrations`` table means the
    migrations never ran.
    """
    conn = await asyncpg.connect(conn_str, timeout=3)
    try:
        rows = await conn.fetch("SELECT filename FROM corpus.schema_migrations ORDER BY filename")
    except asyncpg.UndefinedTableError:
        return None
    finally:
        await conn.close()
    return [str(row["filename"]) for row in rows]


def _read_applied_duckdb_sync(path: str) -> list[str] | None:
    import duckdb

    if not Path(path).exists():
        return None
    con = duckdb.connect(path, read_only=True)
    try:
        rows = con.execute(
            "SELECT filename FROM corpus.schema_migrations ORDER BY filename"
        ).fetchall()
    except duckdb.CatalogException:
        return None
    finally:
        con.close()
    return [str(row[0]) for row in rows]


async def read_applied_duckdb(conn_str: str) -> list[str] | None:
    """The applied DuckDB migration names, or None on a fresh store.

    A missing file, a missing tracking table, or a missing ``corpus``
    schema all mean fresh; other failures escape.
    """
    from corpus_kb.storage.duckdb_support import duckdb_conn_string_to_path

    return await asyncio.to_thread(_read_applied_duckdb_sync, duckdb_conn_string_to_path(conn_str))


async def assert_store_supported(conn_str: str, backend: str) -> StoreVersionReport | None:
    """Refuse a store newer than the code; answer None on a fresh store.

    The one entry the serve paths call. Dispatches on the backend (or
    the DSN scheme), reads the applied set once, and compares it with
    the packaged code set.
    """
    if backend == "duckdb" or conn_str.startswith(DUCKDB_DSN_PREFIX):
        applied = await read_applied_duckdb(conn_str)
    else:
        applied = await read_applied_pg(conn_str)
    if applied is None:
        return None
    return check_store_not_newer(applied, code_migration_names())


__all__ = [
    "StoreVersionReport",
    "assert_store_supported",
    "check_store_not_newer",
    "code_migration_names",
    "migration_number",
    "read_applied_duckdb",
    "read_applied_pg",
]
