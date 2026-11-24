"""Shared machinery for the migration upgrade matrix (W2 compat spine).

Rebuilds a store at a past release snapshot (historical SQL read back
from the git tag), upgrades it with the current runner, and provides the
schema-parity column sets plus the chunk/entity smoke inserts. The test
modules hold the rows; this module holds the machinery.

Postgres scratch databases follow the tests/research_db.py recipe:
pid-suffixed names, extensions as superuser, one control store per
session. DuckDB builds are plain files in the session's temp dirs.
Extension-gated migrations (AGE, pgml) are pre-recorded on Postgres
builds: the compose stack ships both extensions and applies them at
setup, so a compose-store upgrade never re-runs them. On stock Postgres
the shared bootstrap skips them, and no later migration reads either
extension's objects.
"""

from __future__ import annotations

import importlib.resources as resources
import os
import re
import subprocess
import uuid
from pathlib import Path

import asyncpg
import pytest

from corpus_kb._setup.migrate import applied_migrations, ensure_migrations_table
from corpus_kb._setup.schema_apply import EXTENSION_GATED_MIGRATIONS, apply_schema
from corpus_kb._setup.store_width import substitute_dims
from tests import research_db

PG_DIR = "src/corpus_kb/migrations"
DUCKDB_DIR = "src/corpus_kb/migrations_duckdb"
BASE_SCHEMA = "src/corpus_kb/storage/schema.sql"
SEED_TENANT = "00000000-0000-0000-0000-000000000001"

_PG_CONTROLS: dict[str, str] = {}

_REPO_ROOT = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
).stdout.strip()


def slug(anchor: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", anchor.lower()).strip("_")


def git_ref(anchor: str) -> str:
    return "HEAD" if anchor == "head" else anchor


def _git(args: list[str]) -> str | None:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, cwd=_REPO_ROOT)
    return proc.stdout if proc.returncode == 0 else None


def git_show(ref: str, path: str) -> str | None:
    return _git(["show", f"{ref}:{path}"])


def git_show_substituted(ref: str, path: str) -> str | None:
    """Snapshot SQL with the #559 width placeholder at the shipped 2560.

    Anchor replay simulates the legacy regime the way the runners apply
    files: HEAD snapshots carry the placeholder text, which nothing here
    substitutes; older anchors have no placeholder and pass through.
    """
    sql = git_show(ref, path)
    return None if sql is None else substitute_dims(sql, 2560)


def migration_files(ref: str, path: str, kind: str) -> list[str]:
    """Numbered forward migration file names at ``ref``, sorted, or skip."""
    if _git(["rev-parse", "-q", "--verify", f"{ref}^{{commit}}"]) is None:
        pytest.skip(f"anchor snapshot {ref} unreachable (shallow checkout or missing tag)")
    listing = _git(["ls-tree", "--name-only", f"{ref}:{path}"])
    if listing is None:
        pytest.skip(f"no {kind} at anchor {ref}")
    names = [name for name in listing.splitlines() if name[0].isdigit() and name.endswith(".sql")]
    return sorted(names)


def packaged_names(subdir: str) -> set[str]:
    ref = resources.files("corpus_kb") / subdir
    with resources.as_file(ref) as path:
        return {p.name for p in Path(path).iterdir() if p.name[0].isdigit() and p.suffix == ".sql"}


# ---------------------------------------------------------------------------
# Postgres machinery.
# ---------------------------------------------------------------------------


async def create_scratch_db(name: str) -> None:
    admin = await asyncpg.connect(research_db.SUPER_DSN)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}" OWNER {research_db._PG_USER}')
    finally:
        await admin.close()
    superconn = await asyncpg.connect(research_db.super_dsn_for(name))
    try:
        await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    finally:
        await superconn.close()


async def drop_scratch_db(name: str) -> None:
    admin = await asyncpg.connect(research_db.SUPER_DSN)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


def pg_dsn(name: str) -> str:
    return f"{research_db.BASE_DSN}/{name}"


async def build_pg_store_at(ref: str, dsn: str) -> None:
    """Rebuild the store at a release snapshot: base schema, then that
    release's migrations, recorded the way the runner records them."""
    schema_sql = git_show_substituted(ref, BASE_SCHEMA)
    if schema_sql is None:
        pytest.skip(f"anchor snapshot {ref} has no {BASE_SCHEMA}")
    files = migration_files(ref, PG_DIR, "migration set")
    conn = await asyncpg.connect(dsn)
    try:
        try:
            await conn.execute(schema_sql)
        except asyncpg.ProgramLimitExceededError as error:
            # The snapshot's own base schema does not build on this server
            # (an ivfflat index over a wider-than-cap vector column, for
            # one). The runner cannot be exercised against a store this
            # server cannot host; skip honestly instead of patching the
            # snapshot.
            pytest.skip(f"anchor snapshot {ref} base schema does not build on this server: {error}")
        await ensure_migrations_table(conn)
        for name in files:
            if name in EXTENSION_GATED_MIGRATIONS:
                await conn.execute(
                    "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)", name
                )
                continue
            sql = git_show_substituted(ref, f"{PG_DIR}/{name}")
            if sql is None:
                pytest.skip(f"anchor snapshot {ref} is missing migration {name}")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)", name
                )
    finally:
        await conn.close()


async def pg_columns(dsn: str) -> set[tuple[str, ...]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT table_schema, table_name, column_name, data_type, udt_name, is_nullable"
            " FROM information_schema.columns"
            " WHERE table_schema IN ('public', 'corpus', 'corpus_rag')"
        )
    finally:
        await conn.close()
    return {tuple(str(value) for value in row) for row in rows}


async def pg_control() -> str:
    """One control store per session, built fresh from the current set."""
    if "dsn" not in _PG_CONTROLS:
        name = f"corpus_kb_w2m{os.getpid() % 100000:05d}_control"
        await create_scratch_db(name)
        await apply_schema(pg_dsn(name))
        _PG_CONTROLS["dsn"] = pg_dsn(name)
    return _PG_CONTROLS["dsn"]


async def pg_smoke(dsn: str, anchor: str) -> None:
    """One chunk insert plus one graph entity insert on the upgraded store."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"SET app.current_tenant_id = '{SEED_TENANT}'")
        doc_id = await conn.fetchval(
            "INSERT INTO documents (tenant_id, source, source_type)"
            " VALUES ($1, $2, 'text') RETURNING doc_id",
            uuid.UUID(SEED_TENANT),
            f"w2-matrix-{slug(anchor)}",
        )
        await conn.execute(
            "INSERT INTO chunks (tenant_id, doc_id, chunk_index, text) VALUES ($1, $2, 0, $3)",
            uuid.UUID(SEED_TENANT),
            doc_id,
            "upgrade smoke chunk",
        )
        await conn.execute(
            "INSERT INTO entities (tenant_id, name) VALUES ($1, $2)",
            uuid.UUID(SEED_TENANT),
            f"w2-matrix-{slug(anchor)}",
        )
        chunk_count = await conn.fetchval("SELECT count(*) FROM chunks")
        entity_count = await conn.fetchval("SELECT count(*) FROM entities")
    finally:
        await conn.close()
    assert chunk_count is not None and chunk_count >= 1
    assert entity_count is not None and entity_count >= 1


async def pg_tracked(dsn: str) -> set[str]:
    conn = await asyncpg.connect(dsn)
    try:
        return await applied_migrations(conn)
    finally:
        await conn.close()
