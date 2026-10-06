"""Throwaway-database harness for the todo-11 research tests.

Mirrors the spike: creates a scratch database (OWNER corpus_user, extensions
as superuser), applies storage/schema.sql + migrations 001-016 skipping the
extension-gated 004/005 (docker image unavailable on this host — todo-5
substitution precedent), and drops it after the session. Requires Postgres;
tests using it are marked requires_postgres and auto-skip in CI.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import asyncpg

BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
DB_NAME = "corpus_kb_t11_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
SUPER_DSN = f"{BASE_DSN}/postgres"
TEST_TENANT = "00000000-0000-0000-0000-000000000002"

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"

_EXTENSION_GATED = {"004_enable_age.sql", "005_enable_pgml.sql"}


async def create_research_db() -> str:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user')
    await admin.close()

    superconn = await asyncpg.connect(DSN, user="postgres", password="postgres")
    await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    await superconn.close()

    conn = await asyncpg.connect(DSN)
    base_sql = SCHEMA_SQL.read_text(encoding="utf-8")
    base_sql = re.sub(
        r"CREATE INDEX[^;]*ON chunks_vectors USING (ivfflat|hnsw)\s*\([^;]*vector[^;]*;",
        "-- index on vector(4096) omitted: exceeds pgvector 2000-dim limit",
        base_sql,
    )
    await conn.execute(base_sql)
    from corpus_kb._setup.migrate import ensure_migrations_table

    await ensure_migrations_table(conn)
    for path in sorted(MIGRATIONS.iterdir()):
        if path.suffix != ".sql" or not path.name[0].isdigit():
            continue
        if path.name in _EXTENSION_GATED:
            continue
        await conn.execute(path.read_text(encoding="utf-8"))
        await conn.execute(
            "INSERT INTO corpus.schema_migrations (filename) VALUES ($1) ON CONFLICT DO NOTHING",
            path.name,
        )
    await conn.close()
    return DSN


async def drop_research_db() -> None:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    await admin.close()


async def superuser_conn() -> asyncpg.Connection:
    """BYPASSRLS connection for infrastructure verification reads."""
    return await asyncpg.connect(DSN, user="postgres", password="postgres")


def set_env(dsn: str) -> None:
    os.environ["CORPUS_KB_DATABASE_URL"] = dsn
    os.environ["CORPUS_KB_SNAPSHOT_PERIOD"] = "2"


def unset_env() -> None:
    os.environ.pop("CORPUS_KB_DATABASE_URL", None)
    os.environ.pop("CORPUS_KB_SNAPSHOT_PERIOD", None)


async def reset_singletons() -> None:
    from corpus_kb.domain.application import reset_app
    from corpus_kb.handlers.research_handler import reset_research_handler

    reset_app()
    reset_research_handler()
