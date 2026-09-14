"""Idempotent SQL migration runner for Corpus-KB.

Reads .sql files from corpus-kb/migrations/ sorted lexicographically, tracks
applied migrations in corpus.schema_migrations, and runs each unapplied
migration inside a transaction. Re-running is a no-op.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).with_name("..") / "migrations"


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
    rows = await conn.fetch(
        "SELECT filename FROM corpus.schema_migrations ORDER BY filename"
    )
    return {row["filename"] for row in rows}


async def run_migrations(connection_string: str) -> None:
    """Run all unapplied migrations in order."""
    migration_dir = MIGRATIONS_DIR.resolve()
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
            "Example: postgresql://user:pass@localhost:5432/corpus_kb"
        )
    await run_migrations(connection_string)


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    asyncio.run(main())
