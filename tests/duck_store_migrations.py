"""Shared "migrate one fresh DuckDB store" helper for the width tests.

The width-substitution, version-guard, and duck-047 test files all create
a store the same way: the real runner against a scratch file in
``tmp_path``, at the ambient width. One helper pins that pattern so the
three modules cannot drift apart; every assertion stays in the callers.
"""

from __future__ import annotations

from pathlib import Path

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string


async def migrated_duck_store(tmp_path: Path, name: str) -> tuple[Path, str]:
    """Migrate the fresh DuckDB file ``tmp_path/name``; return (path, dsn).

    The DSN is the runner's own connection string for the file, so a
    caller that reopens the store (version guard, migration rerun) does
    not re-derive it.
    """
    path = tmp_path / name
    dsn = duckdb_path_to_conn_string(path)
    await run_migrations(dsn)
    return path, dsn
