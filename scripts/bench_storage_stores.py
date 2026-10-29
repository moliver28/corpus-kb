"""Store lifecycle for the storage benchmark: throwaway isolation per leg.

Postgres: one throwaway database per size, dropped after the leg (reads
respect RLS through the tenant GUC; the seed sets the same GUC). DuckDB:
one throwaway file, deleted after the leg. Postgres roles come from the
environment exactly like the research harness (tests/research_db.py,
storage portability program rule 20), so a compose stack and a local
server both work.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import asyncpg

from corpus_kb._setup.migrate import run_migrations
from corpus_kb._setup.schema_apply import apply_schema
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string


def _pg_ident(name: str) -> str:
    """Pass plain SQL identifiers; refuse everything else."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise SystemExit(f"unsafe SQL identifier: {name!r}")
    return '"' + name + '"'


def _pg_lit(value: str) -> str:
    """Single-quoted SQL string literal (doubling)."""
    return "'" + value.replace("'", "''") + "'"


def pg_env() -> tuple[str, str, str]:
    """(app DSN, superuser DSN, app user), env-derived like research_db.py."""
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "corpus_user")
    password = os.environ.get("POSTGRES_PASSWORD", "corpus_pass")
    base = f"postgresql://{user}:{password}@{host}:{port}"
    super_dsn = (
        f"postgresql://{os.environ.get('POSTGRES_SUPERUSER', 'postgres')}:"
        f"{os.environ.get('POSTGRES_SUPERUSER_PASSWORD', 'postgres')}@{host}:{port}"
    )
    return base, super_dsn, user


async def pg_prepare(base_dsn: str, super_dsn: str, user: str, db: str) -> None:
    """Drop, create and fully schema one throwaway bench database."""
    admin = await asyncpg.connect(super_dsn + "/postgres")
    try:
        statements = {
            "drop_db": "DROP DATABASE IF EXISTS " + _pg_ident(db) + " WITH (FORCE)",
            "create_db": "CREATE DATABASE " + _pg_ident(db) + " OWNER " + _pg_ident(user),
        }
        await admin.execute(statements["drop_db"])
        await admin.execute(statements["create_db"])
    finally:
        await admin.close()
    # The extensions must exist BEFORE apply_schema: storage/schema.sql opens
    # with CREATE EXTENSION, which only a superuser may run (the research
    # harness follows the same order in tests/research_db.py).
    setup = await asyncpg.connect(super_dsn + f"/{db}")
    try:
        await setup.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await setup.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    finally:
        await setup.close()
    await apply_schema(f"{base_dsn}/{db}")


async def pg_drop(super_dsn: str, db: str) -> int:
    """Drop the bench database; return its byte size as measured before."""
    admin = await asyncpg.connect(super_dsn + "/postgres")
    try:
        size = int(await admin.fetchval("SELECT pg_database_size(" + _pg_lit(db) + ")") or 0)
        statements = {"drop_db": "DROP DATABASE IF EXISTS " + _pg_ident(db) + " WITH (FORCE)"}
        await admin.execute(statements["drop_db"])
    finally:
        await admin.close()
    return size


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)
    Path(str(path) + ".wal").unlink(missing_ok=True)


async def duck_prepare(path: Path) -> DuckDbGateway:
    """Delete any prior bench file, apply the mirror migrations, connect."""
    _unlink(path)
    dsn = duckdb_path_to_conn_string(path.resolve())
    await run_migrations(dsn)
    return await DuckDbGateway.connect(dsn)


async def duck_teardown(gateway: DuckDbGateway, path: Path, keep: bool) -> int:
    """Close and (unless kept) delete the bench file; return its byte size."""
    await gateway.close()
    size = path.stat().st_size if path.exists() else 0
    if not keep:
        _unlink(path)
    return size
