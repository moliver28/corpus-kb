"""Idempotent SQL migration runner for Corpus-KB.

Backend-dispatched on the DSN scheme (storage portability program):

- ``duckdb:///...`` names a DuckDB database file. The runner applies
  ``corpus_kb/migrations_duckdb/`` (the full schema mirror, 000..029)
  through a synchronous duckdb connection and tracks applied files in
  that file's own ``schema_migrations`` table.
- anything else is a Postgres DSN. ``migrate_postgres.py`` applies
  ``corpus_kb/migrations/`` through asyncpg, with the issue #348 admin
  knobs (dry run, session settings, superuser handover, the migration-025
  embeddings guard — see ``migrate_admin.py``).

Each runner owns its tracking table; both sort files lexicographically,
apply unapplied files inside a transaction, and are no-ops when re-run.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from corpus_kb._setup.migrate_postgres import (  # noqa: F401 — historical import surface
    applied_migrations,
    ensure_migrations_table,
    prepare_age_session,
    run_postgres_migrations,
)

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


@dataclass
class MigrateReport:
    """What one migrate run found and did (issue #348).

    ``applied`` names the migrations the tracking table held BEFORE the
    run, ``pending`` the ones that were outstanding before it, and
    ``applied_now`` the ones THIS run applied (empty on a dry run).
    """

    backend: str
    applied: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    applied_now: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, object]:
        """The ``--json`` payload: stable key order, plain lists."""
        return {
            "backend": self.backend,
            "applied": self.applied,
            "pending": self.pending,
            "applied_now": self.applied_now,
            "dry_run": self.dry_run,
        }


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


def run_duckdb_migrations(dsn: str, dry_run: bool = False) -> MigrateReport:
    """Run all unapplied DuckDB migrations against the file in ``dsn``.

    Synchronous: the DuckDB client is synchronous (program doc section 2).
    Each migration runs inside one transaction together with its
    ``schema_migrations`` insert, so a failure leaves nothing recorded.
    ``dry_run`` lists the applied and pending names and applies nothing.
    """
    import importlib.resources as resources

    import duckdb

    ref = resources.files("corpus_kb") / "migrations_duckdb"
    with resources.as_file(ref) as migration_dir:
        if not migration_dir.exists():
            logger.warning("DuckDB migrations directory not found: %s", migration_dir)
            return MigrateReport("duckdb")

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
            pending = [f.name for f in sql_files if f.name not in done]
            if dry_run:
                return MigrateReport("duckdb", sorted(done), pending, [], True)

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
            return MigrateReport("duckdb", sorted(done), pending, pending, False)
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def run_migrations(
    connection_string: str,
    *,
    superuser_dsn: str | None = None,
    session_settings: dict[str, str] | None = None,
    reset_embeddings: bool = False,
    dry_run: bool = False,
) -> MigrateReport:
    """Run all unapplied migrations in order on the backend the DSN names.

    Issue #348 admin knobs:

    - ``dry_run``: report the applied and pending names, apply nothing.
    - ``session_settings``: SET on the migration session before the heavy
      migrations run (0.6.0 needed ``work_mem=1GB`` for migration 015's
      quadratic ``NOT IN`` dedupe on large studies).
    - ``superuser_dsn``: run the pending migrations as that role, then
      hand ownership and grants to the app role (the user in
      ``connection_string``) — the 0.6.0 plugin-SQL behavior, so every
      client gets it.
    - ``reset_embeddings``: accept the migration-025 data decision that
      discards embeddings stored in the old ``vector`` column.
    """
    if connection_string.startswith("duckdb:"):
        return await asyncio.to_thread(run_duckdb_migrations, connection_string, dry_run)
    return await run_postgres_migrations(
        connection_string,
        superuser_dsn=superuser_dsn,
        session_settings=session_settings,
        reset_embeddings=reset_embeddings,
        dry_run=dry_run,
    )


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
