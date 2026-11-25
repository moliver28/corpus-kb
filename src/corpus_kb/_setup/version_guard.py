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

Issue #559 adds the second refusal: a store reopened with an
``embedding.dimensions`` other than its recorded width raises the typed
:class:`~corpus_kb.errors.ConfigError`, one message on both engines.
"""

from __future__ import annotations

import asyncio
import importlib.resources as resources
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from corpus_kb._setup.migrate import DUCKDB_DSN_PREFIX
from corpus_kb.errors import DatabaseUnavailableError, StoreNewerError

logger = logging.getLogger(__name__)


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


def code_migration_names(backend: str) -> list[str]:
    """The packaged forward migration filenames for ``backend``, sorted.

    The two sets number independently (issue #230), so a DuckDB store
    must compare against ``migrations_duckdb/`` — its 048 row measured
    against a PG-terminated name set would refuse in error.
    """
    directory = "migrations_duckdb" if backend == "duckdb" else "migrations"
    ref = resources.files("corpus_kb") / directory
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

    from corpus_kb.storage.duckdb_support import map_connect_error

    if not Path(path).exists():
        return None
    try:
        con = duckdb.connect(path, read_only=True)
    except Exception as exc:
        raise map_connect_error(exc) from exc
    try:
        # Migration names are UNQUALIFIED (main); the corpus. spell is the fallback.
        try:
            rows = con.execute(
                "SELECT filename FROM schema_migrations ORDER BY filename"
            ).fetchall()
        except duckdb.CatalogException:
            try:
                rows = con.execute(
                    "SELECT filename FROM corpus.schema_migrations ORDER BY filename"
                ).fetchall()
            except (duckdb.CatalogException, duckdb.BinderException):
                return None
        except duckdb.BinderException:
            return None
    finally:
        con.close()
    return [str(row[0]) for row in rows]


async def read_applied_duckdb(conn_str: str) -> list[str] | None:
    """The applied DuckDB migration names, or None on a fresh store.
    Unqualified ``main`` first, ``corpus`` fallback; fresh on a missing file or table.
    """
    from corpus_kb.storage.duckdb_support import duckdb_conn_string_to_path

    return await asyncio.to_thread(_read_applied_duckdb_sync, duckdb_conn_string_to_path(conn_str))


async def _store_width(conn_str: str, backend: str) -> int:
    """The width the store was migrated with, recorded lazily when absent.

    An absent meta row is a pre-parameterization store: the live column
    width — or the legacy 2560 default — is written back, so every later
    boot compares recorded numbers (issue #559).
    """
    from corpus_kb._setup import store_width as sw

    is_duck = backend == "duckdb" or conn_str.startswith(DUCKDB_DSN_PREFIX)
    if is_duck:
        from corpus_kb.storage.duckdb_support import duckdb_conn_string_to_path

        path = duckdb_conn_string_to_path(conn_str)
        stored, live = await asyncio.to_thread(sw.probe_store_width_duck_sync, path)
    else:
        conn = await asyncpg.connect(conn_str, timeout=3)
        try:
            stored = await sw.read_meta_width_pg(conn)
            live = await sw.store_embedding_width_pg(conn)
        finally:
            await conn.close()
    if stored is not None:
        return int(stored)
    effective = live if live is not None else sw.LEGACY_STORE_WIDTH
    if is_duck:
        recorded = await asyncio.to_thread(sw.backfill_store_width_duck_sync, path, effective)
    else:
        conn = await asyncpg.connect(conn_str, timeout=3)
        try:
            await sw.write_meta_width_pg(conn, effective)
            recorded = True
        except asyncpg.PostgresError:
            recorded = False
        finally:
            await conn.close()
    if recorded:
        logger.info("Recorded the store embedding width %d in the store meta table.", effective)
    else:
        logger.warning("Could not record the store embedding width %d.", effective)
    return effective


async def assert_store_supported(conn_str: str, backend: str) -> StoreVersionReport | None:
    """Refuse a store newer than the code; answer None on a fresh store.

    The one entry the serve paths call. Dispatches on the backend (or
    the DSN scheme), reads the applied set once, and compares it with
    the packaged code set. An unreachable database becomes the typed
    ``DatabaseUnavailableError`` here, so every serve path answers one
    failure class without repeating the wrap. Issue #559 adds the width
    refusal: one identical ``ConfigError`` message on both engines.
    """
    from corpus_kb._setup.store_width import configured_embedding_dims
    from corpus_kb.config_gates import store_width_mismatch_error
    from corpus_kb.errors import ConfigError

    try:
        if backend == "duckdb" or conn_str.startswith(DUCKDB_DSN_PREFIX):
            applied = await read_applied_duckdb(conn_str)
        else:
            applied = await read_applied_pg(conn_str)
        if applied is not None:
            is_duck = backend == "duckdb" or conn_str.startswith(DUCKDB_DSN_PREFIX)
            code_backend = "duckdb" if is_duck else "postgres"
            report = check_store_not_newer(applied, code_migration_names(code_backend))
            effective = await _store_width(conn_str, backend)
            message = store_width_mismatch_error(configured_embedding_dims(), effective)
            if message is not None:
                raise ConfigError(message)
            return report
        return None
    except (ConnectionError, OSError, asyncpg.PostgresError) as exc:
        detail = str(exc) or type(exc).__name__
        raise DatabaseUnavailableError(
            f"Cannot reach the database to check the schema version: {detail}"
        ) from exc


__all__ = [
    "StoreVersionReport",
    "assert_store_supported",
    "check_store_not_newer",
    "code_migration_names",
    "migration_number",
    "read_applied_duckdb",
    "read_applied_pg",
]
