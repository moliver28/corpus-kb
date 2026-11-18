"""The Postgres migration runner (issue #348 admin flow).

Plan → guard → apply, superuser-aware: without a superuser DSN the
pending migrations run as the configured role; with one, they run as the
superuser and the ownership handover (``migrate_admin.py``) moves the
created objects and grants to the app role afterwards. The dispatching
``run_migrations`` and the DuckDB runner live in ``migrate.py``.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import asyncpg

from corpus_kb._setup.migrate_admin import (
    apply_session_settings,
    dsn_role,
    hand_over_to_app_role,
    refuse_025_with_embeddings,
)
from corpus_kb._setup.migrate_report import GATED_MIGRATION_EXTENSIONS, MigrateReport

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


async def prepare_age_session(conn: asyncpg.Connection) -> bool:
    """Put a live connection into the session state AGE DDL needs.

    Apache AGE resolves its graphid_ops operator class through the caller's
    search_path (apache/age#2504), so migration 004 fails without this
    ritual even when the extension is installed. ag_catalog sits LAST on
    the path on purpose: unqualified migration DDL must keep creating in
    ``public``, while the opclass, cypher() and agtype still resolve
    through the path. Returns False when the AGE shared library is
    absent or the role may not load it, leaving the connection untouched.
    """
    try:
        await conn.execute("LOAD 'age'")
    except (asyncpg.UndefinedFileError, asyncpg.InsufficientPrivilegeError):
        return False
    await conn.execute('SET search_path = "$user", public, ag_catalog')
    return True


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


async def _missing_gated_extensions(conn: asyncpg.Connection) -> set[str]:
    """The gated extensions this server cannot install (issue #508).

    One read-only probe of ``pg_available_extensions`` per run; the
    result serves every gated file, so no per-file guessing.
    """
    rows = await conn.fetch("SELECT name FROM pg_available_extensions")
    available = {row["name"] for row in rows}
    return {ext for ext in GATED_MIGRATION_EXTENSIONS.values() if ext not in available}


def _split_pending(
    sql_files: list[Path], done: set[str], missing: set[str]
) -> tuple[list[str], list[str]]:
    """(pending, skipped) names for this run.

    A gated file whose extension is missing is reported as skipped, not
    pending, so a fully migrated native database reports no pending work.
    """
    pending: list[str] = []
    skipped: list[str] = []
    for sql_file in sql_files:
        if sql_file.name in done:
            continue
        extension = GATED_MIGRATION_EXTENSIONS.get(sql_file.name)
        if extension is not None and extension in missing:
            skipped.append(sql_file.name)
        else:
            pending.append(sql_file.name)
    return pending, skipped


async def run_postgres_migrations(
    connection_string: str,
    *,
    superuser_dsn: str | None = None,
    session_settings: dict[str, str] | None = None,
    reset_embeddings: bool = False,
    dry_run: bool = False,
) -> MigrateReport:
    """The Postgres path: plan, guard, then apply (superuser-aware)."""
    import importlib.resources as resources

    ref = resources.files("corpus_kb") / "migrations"
    with resources.as_file(ref) as migration_dir:
        if not migration_dir.exists():
            logger.warning("Migrations directory not found: %s", migration_dir)
            return MigrateReport("postgres", dry_run=dry_run)

        sql_files: list[Path] = sorted(
            p
            for p in migration_dir.iterdir()
            if p.is_file() and p.suffix == ".sql" and re.match(r"^\d+_", p.name)
        )

        conn = await asyncpg.connect(connection_string)
        try:
            await prepare_age_session(conn)
            await ensure_migrations_table(conn)
            done = await applied_migrations(conn)
            missing = await _missing_gated_extensions(conn)
            pending, skipped = _split_pending(sql_files, done, missing)
            if dry_run:
                return MigrateReport(
                    "postgres",
                    sorted(done),
                    pending,
                    [],
                    True,
                    skipped_extension_gated=skipped,
                )
            await refuse_025_with_embeddings(conn, pending, reset_embeddings)
            if superuser_dsn is None:
                await apply_session_settings(conn, session_settings)
                applied_now, applied_skipped = await _apply_pending(conn, sql_files, missing)
                logger.info("Migrations complete")
                return MigrateReport(
                    "postgres",
                    sorted(done),
                    pending,
                    applied_now,
                    False,
                    skipped_extension_gated=applied_skipped,
                )
        finally:
            await conn.close()

        # Privileged path: run the pending migrations as the superuser, then
        # hand ownership and grants to the app role (issue #348).
        su_conn = await asyncpg.connect(superuser_dsn)
        try:
            await prepare_age_session(su_conn)
            await ensure_migrations_table(su_conn)
            await apply_session_settings(su_conn, session_settings)
            applied_now, applied_skipped = await _apply_pending(su_conn, sql_files, missing)
            await hand_over_to_app_role(
                su_conn,
                superuser_role=dsn_role(superuser_dsn),
                app_role=dsn_role(connection_string),
            )
        finally:
            await su_conn.close()
        logger.info("Migrations complete")
        return MigrateReport(
            "postgres",
            sorted(done),
            pending,
            applied_now,
            False,
            skipped_extension_gated=applied_skipped,
        )


async def _apply_pending(
    conn: asyncpg.Connection, sql_files: list[Path], missing: set[str]
) -> tuple[list[str], list[str]]:
    """Apply every unapplied migration file in order; return (applied, skipped).

    A gated migration whose extension is missing is skipped with a loud
    warning and never recorded as applied (issue #508).
    """
    done = await applied_migrations(conn)
    applied_now: list[str] = []
    skipped: list[str] = []
    for sql_file in sql_files:
        if sql_file.name in done:
            logger.info("Migration already applied: %s", sql_file.name)
            continue
        extension = GATED_MIGRATION_EXTENSIONS.get(sql_file.name)
        if extension is not None and extension in missing:
            logger.warning(
                "Skipping extension-gated migration %s "
                "(needs the %s extension; the docker-compose stack ships both)",
                sql_file.name,
                extension,
            )
            skipped.append(sql_file.name)
            continue
        sql = sql_file.read_text(encoding="utf-8")
        async with conn.transaction():
            logger.info("Applying migration: %s", sql_file.name)
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)",
                sql_file.name,
            )
        applied_now.append(sql_file.name)
    return applied_now, skipped
