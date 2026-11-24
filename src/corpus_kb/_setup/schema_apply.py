"""Shared schema bootstrap: storage/schema.sql plus the numbered migrations.

ONE implementation for ``corpus-kb quickstart`` and the research test
harness (``tests/research_db.py``), so the two paths cannot drift.

Applies storage/schema.sql verbatim (every statement is idempotent),
then every numbered migration not yet recorded in
``corpus.schema_migrations``. Extension-gated migrations (AGE, pgml)
are skipped with a loud warning, never applied, never recorded; the
docker-compose stack ships both extensions and applies them there.

Re-running is a no-op: applied migration names are read from the
tracking table and skipped. Each migration runs inside a transaction.
"""

from __future__ import annotations

import importlib.resources as resources
import logging
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

from corpus_kb._setup.migrate import applied_migrations, ensure_migrations_table

# EXTENSION_GATED_MIGRATIONS is re-exported for existing readers (issue #508);
# its owning definition lives in migrate_report.
from corpus_kb._setup.migrate_report import EXTENSION_GATED_MIGRATIONS
from corpus_kb._setup.store_width import (
    configured_embedding_dims,
    record_applied_width_pg,
    substitute_dims,
)

logger = logging.getLogger(__name__)


@dataclass
class SchemaApplyResult:
    """What one :func:`apply_schema` run did to the database."""

    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def packaged_schema_path() -> Path:
    """The packaged storage/schema.sql file."""
    ref = resources.files("corpus_kb") / "storage" / "schema.sql"
    with resources.as_file(ref) as path:
        return Path(path)


def packaged_migrations_dir() -> Path:
    """The packaged corpus_kb/migrations directory."""
    ref = resources.files("corpus_kb") / "migrations"
    with resources.as_file(ref) as path:
        return Path(path)


def _numbered_sql(directory: Path) -> list[Path]:
    """Numbered migration files, sorted; rollback_* files are excluded."""
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix == ".sql" and p.name[0].isdigit()
    )


async def _needs_base_schema(conn: asyncpg.Connection) -> bool:
    """True when storage/schema.sql has not been applied to this database.

    The file seeds the default tenant BEFORE migration 011 forces RLS on
    the table, so a re-run as the non-superuser app role would violate
    that policy. The file is atomic (asyncpg runs one multi-statement
    string in a single implicit transaction), so the documents table
    existing means the whole file applied.
    """
    row = await conn.fetchval(
        "SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'documents'"
    )
    return row is None


async def apply_schema(
    connection_string: str,
    *,
    schema_path: Path | None = None,
    migrations_dir: Path | None = None,
    embedding_dims: int | None = None,
) -> SchemaApplyResult:
    """Apply storage/schema.sql, then every unapplied numbered migration.

    The base schema file runs once (see :func:`_needs_base_schema`);
    extension-gated migrations are skipped with a loud warning (they
    need the AGE/pgml server extensions). Migration names already
    recorded in ``corpus.schema_migrations`` are skipped, so a re-run
    touches nothing.

    The ``__EMBEDDING_DIMS__`` placeholder in the base file and the
    migrations is substituted with ``embedding_dims`` (default: the
    configured ``embedding.dimensions``), and the width this run leaves
    ``chunks_vectors`` with is recorded in the store meta table
    (issue #559).
    """
    dims = embedding_dims if embedding_dims is not None else configured_embedding_dims()
    schema = schema_path or packaged_schema_path()
    result = SchemaApplyResult()
    conn = await asyncpg.connect(connection_string)
    try:
        base_applied = await _needs_base_schema(conn)
        if base_applied:
            await conn.execute(substitute_dims(schema.read_text(encoding="utf-8"), dims))
        await ensure_migrations_table(conn)
        done = await applied_migrations(conn)
        for path in _numbered_sql(migrations_dir or packaged_migrations_dir()):
            if path.name in EXTENSION_GATED_MIGRATIONS:
                logger.warning(
                    "Skipping extension-gated migration %s "
                    "(needs AGE/pgml; the docker-compose stack ships both)",
                    path.name,
                )
                result.skipped.append(path.name)
                continue
            if path.name in done:
                continue
            async with conn.transaction():
                await conn.execute(substitute_dims(path.read_text(encoding="utf-8"), dims))
                await conn.execute(
                    "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)",
                    path.name,
                )
            result.applied.append(path.name)
        if base_applied or result.applied:
            recorded = await record_applied_width_pg(conn)
            if recorded is not None:
                logger.info("Recorded the store embedding width: %d", recorded)
    finally:
        await conn.close()
    return result
