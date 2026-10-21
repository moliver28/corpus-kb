"""Throwaway-database harness for the research tests.

Mirrors the spike: creates a scratch database (OWNER corpus_user, extensions
as superuser), applies storage/schema.sql + the numbered migrations through
the SHARED bootstrap (corpus_kb._setup.schema_apply — quickstart uses the
same code, so the two cannot drift), skipping the extension-gated
004/005, and drops it after the session. Requires Postgres; tests using
it are marked requires_postgres and auto-skip in CI.
"""

from __future__ import annotations

import os
import re

import asyncpg

BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
# Overridable so two sessions running live tests at once stop force-dropping
# each other's throwaway database (set CORPUS_KB_TEST_DB per session).
DB_NAME = os.environ.get("CORPUS_KB_TEST_DB", "corpus_kb_t11_test")
DSN = f"{BASE_DSN}/{DB_NAME}"
SUPER_DSN = f"{BASE_DSN}/postgres"
TEST_TENANT = "00000000-0000-0000-0000-000000000002"

# DDL identifiers cannot be bound; guard the constant instead.
if not re.fullmatch(r"[a-z_0-9]+", DB_NAME):
    raise ValueError(f"DB_NAME must be a bare identifier, got {DB_NAME!r}")


async def create_research_db() -> str:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user')
    await admin.close()

    superconn = await asyncpg.connect(DSN, user="postgres", password="postgres")
    await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    await superconn.close()

    # The halfvec(2560) schema (migration 025 regime) carries only LEGAL
    # indexes (halfvec caps at 4000 dims), so the file executes verbatim.
    # Extension-gated 004/005 are skipped with a loud warning by the shared
    # bootstrap; applied names are recorded in corpus.schema_migrations.
    from corpus_kb._setup.schema_apply import apply_schema

    await apply_schema(DSN)
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
