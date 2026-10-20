"""Throwaway-database harness for the research tests.

Mirrors the spike: creates a scratch database (OWNER corpus_user, extensions
as superuser), applies storage/schema.sql + the numbered migrations through
the SHARED bootstrap (corpus_kb._setup.schema_apply — quickstart uses the
same code, so the two cannot drift), skipping the extension-gated
004/005, and drops it after the session. Requires Postgres; tests using
it are marked requires_postgres and auto-skip in CI.

The connection settings are env-derived (storage portability program rule
20) so the CI Postgres service and a local compose stack both work:
POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD carry the
app role, POSTGRES_SUPERUSER / POSTGRES_SUPERUSER_PASSWORD the admin role
that creates databases and extensions. The historical localhost fallback is
preserved for local runs. In the CI service image the bootstrap role
(corpus_user) IS the superuser, so both roles resolve to it there.
"""

from __future__ import annotations

import os
import re

import asyncpg

_PG_HOST = os.environ.get("POSTGRES_HOST", "localhost")
_PG_PORT = os.environ.get("POSTGRES_PORT", "5432")
_PG_USER = os.environ.get("POSTGRES_USER", "corpus_user")
_PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "corpus_pass")
_SUPERUSER = os.environ.get("POSTGRES_SUPERUSER", "postgres")
_SUPERUSER_PASSWORD = os.environ.get("POSTGRES_SUPERUSER_PASSWORD", "postgres")

BASE_DSN = f"postgresql://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}"
SUPER_DSN = f"postgresql://{_SUPERUSER}:{_SUPERUSER_PASSWORD}@{_PG_HOST}:{_PG_PORT}"
DB_NAME = "corpus_kb_t11_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
TEST_TENANT = "00000000-0000-0000-0000-000000000002"

# DDL identifiers cannot be bound; guard the constants instead.
if not re.fullmatch(r"[a-z_0-9]+", DB_NAME):
    raise ValueError(f"DB_NAME must be a bare identifier, got {DB_NAME!r}")
if not re.fullmatch(r"[a-z_0-9]+", _PG_USER):
    raise ValueError(f"POSTGRES_USER must be a bare identifier, got {_PG_USER!r}")


async def create_research_db() -> str:
    admin = await asyncpg.connect(SUPER_DSN)
    await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{DB_NAME}" OWNER {_PG_USER}')
    await admin.close()

    # Extension setup connects to the SCRATCH database as the superuser (the
    # pre-portability behavior: locally corpus_user is not a superuser; in
    # the CI service image both roles resolve to the bootstrap superuser).
    # DB_NAME is read at call time so monkeypatched scratch names keep working.
    superconn = await asyncpg.connect(f"{SUPER_DSN}/{DB_NAME}")
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
    admin = await asyncpg.connect(SUPER_DSN)
    await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    await admin.close()


async def superuser_conn() -> asyncpg.Connection:
    """BYPASSRLS connection for infrastructure verification reads."""
    return await asyncpg.connect(DSN)


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
