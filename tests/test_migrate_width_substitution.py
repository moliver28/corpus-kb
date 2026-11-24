"""Width placeholder substitution in the migration runners (issue #559 / G28).

The migration DDL carries ``__EMBEDDING_DIMS__`` where the primary width
belongs; the runners substitute the configured ``embedding.dimensions``
(the ``CORPUS_KB_EMBEDDING_DIMENSIONS`` env override flows through
``load_config``). These tests migrate a real DuckDB file at a non-default
width, prove the columns and the meta row follow, and pin the
enumeration: no primary-width literal survives as bare DDL.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb._setup.store_width import EMBEDDING_DIMS_PLACEHOLDER
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

MIGRATION_DIRS = (
    "src/corpus_kb/migrations",
    "src/corpus_kb/migrations_duckdb",
)

# The exact non-comment lines that may still carry "2560" after the
# parameterization — the pinned enumeration the plan requires. None of
# them is primary-width column DDL:
PINNED_2560_LINES = frozenset(
    {
        # HNSW index NAME (an identifier, not column DDL; PG 025 + rollback).
        "EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_vectors_hnsw_halfvec2560 '",
        "RAISE WARNING 'Skipping idx_chunks_vectors_hnsw_halfvec2560: %', SQLERRM;",
        "DROP INDEX IF EXISTS idx_chunks_vectors_hnsw_halfvec2560;",
        # embedding_cache_2560 (migration 046, both engines + rollbacks): a
        # derived fixed-width cache with its own runtime width-skip guard
        # (rag/embedder_cache.py SCHEMA_EMBEDDING_WIDTH); embedders at another
        # width bypass it, so the column is not the primary-width column.
        "CREATE TABLE IF NOT EXISTS embedding_cache_2560 (",
        "vector halfvec(2560) NOT NULL,",
        "vector FLOAT[2560] NOT NULL,",
        "DROP TABLE IF EXISTS embedding_cache_2560;",
        # The meta table's default row: covers stores migrated BEFORE the
        # width row existed (every pre-parameterization store is 2560 by
        # construction); the runner's observed-width write overrides it.
        "VALUES ('embedding_width', '2560')",
        # rollback_025's catalog guards: operator-side rollback only restores
        # the legacy 4096 shape from the shipped 2560 regime (revert = 2560-only).
        "IF col_type = 'halfvec(2560)' THEN",
    }
)


async def test_duck_migrate_substitutes_width(tmp_path, monkeypatch) -> None:
    """Env width 1024 migrates a real duck file to FLOAT[1024] columns."""
    monkeypatch.setenv("CORPUS_KB_EMBEDDING_DIMENSIONS", "1024")
    path = tmp_path / "width1024.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    con = duckdb.connect(str(path), read_only=True)
    try:
        for table, column in (
            ("chunks_vectors", "embedding"),
            ("code_registry", "definition_vector"),
            ("meta_embeddings", "embedding"),
        ):
            row = con.execute(
                "SELECT data_type FROM duckdb_columns() WHERE table_name = ? AND column_name = ?",
                [table, column],
            ).fetchone()
            assert row is not None, f"{table}.{column} missing"
            assert row[0] == "FLOAT[1024]", f"{table}.{column} is {row[0]}"
    finally:
        con.close()


async def test_duck_migrate_default_2560(tmp_path, monkeypatch) -> None:
    """Without the env override the DDL substitutes the shipped 2560."""
    monkeypatch.delenv("CORPUS_KB_EMBEDDING_DIMENSIONS", raising=False)
    path = tmp_path / "default.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    con = duckdb.connect(str(path), read_only=True)
    try:
        row = con.execute(
            "SELECT data_type FROM duckdb_columns() "
            "WHERE table_name = 'chunks_vectors' AND column_name = 'embedding'"
        ).fetchone()
    finally:
        con.close()
    assert row is not None and row[0] == "FLOAT[2560]"


async def test_store_meta_records_width_duck(tmp_path, monkeypatch) -> None:
    """The runner records the substituted width; a no-op rerun keeps it."""
    monkeypatch.setenv("CORPUS_KB_EMBEDDING_DIMENSIONS", "1024")
    path = tmp_path / "meta.duckdb"
    dsn = duckdb_path_to_conn_string(path)
    await run_migrations(dsn)
    con = duckdb.connect(str(path), read_only=True)
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'embedding_width'").fetchone()
    finally:
        con.close()
    assert row is not None and row[0] == "1024"
    # A rerun applies nothing, so it must not rewrite the recorded width.
    monkeypatch.setenv("CORPUS_KB_EMBEDDING_DIMENSIONS", "2560")
    await run_migrations(dsn)
    con = duckdb.connect(str(path), read_only=True)
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'embedding_width'").fetchone()
    finally:
        con.close()
    assert row is not None and row[0] == "1024"


def test_pg_placeholder_enumerated() -> None:
    """Zero bare 2560 column DDL stays outside the pinned enumeration.

    Comment lines pass; every non-comment line carrying "2560" must match
    the pinned list above (each entry carries its rationale there).
    """
    offenders: list[str] = []
    placeholders: set[str] = set()
    for directory in MIGRATION_DIRS:
        for sql_file in sorted((Path(directory)).glob("*.sql")):
            if sql_file.name.startswith("rollback_"):
                continue  # operator-side mirrors; pins above cover their literals
            for line in sql_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                is_comment = stripped.startswith("--")
                if "2560" in stripped and not is_comment and stripped not in PINNED_2560_LINES:
                    offenders.append(f"{sql_file.as_posix()}: {stripped}")
                if EMBEDDING_DIMS_PLACEHOLDER in stripped and not is_comment:
                    placeholders.add(sql_file.name)
    assert offenders == []
    # The placeholder lives in exactly the primary-width files: the PG
    # retype/cache-free set and the DuckDB mirror set.
    assert placeholders == {
        "025_halfvec_2560.sql",  # PG (retype DDL) — duck twin is comments-only
        "034_meta_embeddings.sql",
        "000_base_schema.sql",
        "012_coding_schema.sql",
        "035_meta_embeddings.sql",
        "047_meta_embeddings_checks.sql",
    }


def test_base_schema_carries_the_placeholder() -> None:
    """storage/schema.sql shapes fresh PG stores: its column substitutes too.

    The base file is the PG fresh-install width lever (migration 025's
    retype no-ops there), so the parameterization must cover it.
    """
    base = Path("src/corpus_kb/storage/schema.sql")
    assert "embedding halfvec(__EMBEDDING_DIMS__)," in base.read_text(encoding="utf-8")


@pytest.mark.parametrize("sql", ["047_store_meta.sql", "048_store_meta.sql"])
def test_store_meta_migrations_and_rollback_mirrors_exist(sql: str) -> None:
    """Both engines carry the meta migration and its DROP mirror."""
    engine_dir = "migrations" if sql.startswith("047") else "migrations_duckdb"
    number = sql[:3]
    forward = Path("src/corpus_kb") / engine_dir / sql
    mirror = Path("src/corpus_kb") / engine_dir / f"rollback_{number}_store_meta.sql"
    forward_text = forward.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS" in forward_text
    assert "embedding_width" in forward_text
    assert "DROP TABLE IF EXISTS" in mirror.read_text(encoding="utf-8")
