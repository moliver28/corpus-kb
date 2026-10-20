"""Idempotent SQL migration runner for Corpus-KB.

Backend-dispatched on the DSN scheme (storage portability program):

- ``duckdb:///...`` names a DuckDB database file. The runner applies
  ``corpus_kb/migrations_duckdb/`` (the full schema mirror, 000..026)
  through a synchronous duckdb connection and tracks applied files in
  that file's own ``schema_migrations`` table.
- anything else is a Postgres DSN. The Postgres path applies
  ``corpus_kb/migrations/`` through asyncpg, exactly as before.

Each runner owns its tracking table; both sort files lexicographically,
apply unapplied files inside a transaction, and are no-ops when re-run.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

#: DSN prefix that selects the DuckDB backend (program doc section 3).
#: ``duckdb:///C:/data/corpus.duckdb`` (Windows drive) and
#: ``duckdb:///data/corpus.duckdb`` (relative) both route here.
DUCKDB_DSN_PREFIX = "duckdb:///"

_DUCKDB_MIGRATIONS_TABLE_SQL = """
CREATE SCHEMA IF NOT EXISTS corpus;
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename VARCHAR PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
);
"""


def duckdb_dsn_to_path(dsn: str) -> str:
    """Strip the ``duckdb:///`` scheme from a DSN, returning the file path.

    Local copy of the path convention the storage gateway owns (program
    doc section 3.4): a Windows drive letter keeps its colon, a leading
    slash marks a POSIX absolute path, anything else is relative to the
    process working directory.
    """
    if not dsn.startswith(DUCKDB_DSN_PREFIX):
        raise ValueError("Not a DuckDB DSN. Use the form duckdb:///C:/data/corpus.duckdb")
    path = dsn[len(DUCKDB_DSN_PREFIX) :]
    if not path:
        raise ValueError("The DuckDB DSN names no file. Use duckdb:///C:/data/corpus.duckdb")
    return path


# ---------------------------------------------------------------------------
# DuckDB backend
# ---------------------------------------------------------------------------


def _ensure_duckdb_migrations_table(con: duckdb.DuckDBPyConnection) -> None:
    """Create the DuckDB-side schema_migrations tracking table."""
    con.execute(_DUCKDB_MIGRATIONS_TABLE_SQL)


def _applied_duckdb_migrations(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Return the set of migration filenames already applied on DuckDB."""
    rows = con.execute("SELECT filename FROM schema_migrations ORDER BY filename").fetchall()
    return {row[0] for row in rows}


def run_duckdb_migrations(dsn: str) -> None:
    """Run all unapplied DuckDB migrations against the file in ``dsn``.

    Synchronous: the DuckDB client is synchronous (program doc section 2).
    Each migration runs inside one transaction together with its
    ``schema_migrations`` insert, so a failure leaves nothing recorded.
    """
    import importlib.resources as resources

    import duckdb

    ref = resources.files("corpus_kb") / "migrations_duckdb"
    with resources.as_file(ref) as migration_dir:
        if not migration_dir.exists():
            logger.warning("DuckDB migrations directory not found: %s", migration_dir)
            return

        sql_files = sorted(
            p
            for p in migration_dir.iterdir()
            if p.is_file() and p.suffix == ".sql" and re.match(r"^\d+_", p.name)
        )

        path = duckdb_dsn_to_path(dsn)
        con: duckdb.DuckDBPyConnection = duckdb.connect(path)
        try:
            _ensure_duckdb_migrations_table(con)
            done = _applied_duckdb_migrations(con)

            for sql_file in sql_files:
                if sql_file.name in done:
                    logger.info("Migration already applied: %s", sql_file.name)
                    continue

                sql = sql_file.read_text(encoding="utf-8")
                logger.info("Applying DuckDB migration: %s", sql_file.name)
                con.execute("BEGIN TRANSACTION")
                try:
                    con.execute(sql)
                    con.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (?)",
                        [sql_file.name],
                    )
                    con.execute("COMMIT")
                except Exception:
                    con.execute("ROLLBACK")
                    raise
            logger.info("DuckDB migrations complete")
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Postgres backend (unchanged path)
# ---------------------------------------------------------------------------


async def ensure_migrations_table(conn: asyncpg.Connection) -> None:
    """Create the schema_migrations tracking table if it does not exist."""
    await conn.execute(
        """
        CREATE SCHEMA IF NOT EXISTS corpus;
        CREATE TABLE IF NOT EXISTS corpus.schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT now()
        );
        """
    )


async def applied_migrations(conn: asyncpg.Connection) -> set[str]:
    """Return the set of migration filenames already applied."""
    rows = await conn.fetch("SELECT filename FROM corpus.schema_migrations ORDER BY filename")
    return {row["filename"] for row in rows}


async def run_migrations(connection_string: str) -> None:
    """Run all unapplied migrations in order on the backend the DSN names."""
    if connection_string.startswith("duckdb:"):
        await asyncio.to_thread(run_duckdb_migrations, connection_string)
        return

    import importlib.resources as resources

    ref = resources.files("corpus_kb") / "migrations"
    with resources.as_file(ref) as migration_dir:
        if not migration_dir.exists():
            logger.warning("Migrations directory not found: %s", migration_dir)
            return

        sql_files = sorted(
            p
            for p in migration_dir.iterdir()
            if p.is_file() and p.suffix == ".sql" and re.match(r"^\d+_", p.name)
        )

        conn = await asyncpg.connect(connection_string)
        try:
            await ensure_migrations_table(conn)
            done = await applied_migrations(conn)

            for sql_file in sql_files:
                if sql_file.name in done:
                    logger.info("Migration already applied: %s", sql_file.name)
                    continue

                sql = sql_file.read_text(encoding="utf-8")
                async with conn.transaction():
                    logger.info("Applying migration: %s", sql_file.name)
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)",
                        sql_file.name,
                    )
            logger.info("Migrations complete")
        finally:
            await conn.close()


async def main() -> None:
    """CLI entry point: read CORPUS_KB_DATABASE_URL from environment."""
    import os

    connection_string = os.environ.get("CORPUS_KB_DATABASE_URL", "")
    if not connection_string:
        raise SystemExit(
            "Set CORPUS_KB_DATABASE_URL to run migrations.\n"
            "Example: postgresql://user:pass@localhost:5432/corpus_kb\n"
            "DuckDB example: duckdb:///C:/data/corpus.duckdb"
        )
    await run_migrations(connection_string)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    asyncio.run(main())
