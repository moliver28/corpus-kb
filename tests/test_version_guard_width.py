"""The boot guard's width refusal and lazy meta-row backfill (#559 / G28).

A store reopened with a configured ``embedding.dimensions`` other than
the recorded width raises the typed ``ConfigError`` — the same message on
both engines. A pre-parameterization store (no meta row) is treated as
the shipped 2560, and the row is backfilled with one info line.
"""

from __future__ import annotations

import duckdb
import pytest
from duck_store_migrations import migrated_duck_store

from corpus_kb._setup.version_guard import assert_store_supported
from corpus_kb.errors import ConfigError


async def test_reopening_with_other_width_refuses(tmp_path, monkeypatch) -> None:
    """Migrate at 2560, reopen at 1024: ConfigError with the R5 message."""
    monkeypatch.delenv("CORPUS_KB_EMBEDDING_DIMENSIONS", raising=False)
    _, dsn = await migrated_duck_store(tmp_path, "store2560.duckdb")
    monkeypatch.setenv("CORPUS_KB_EMBEDDING_DIMENSIONS", "1024")
    with pytest.raises(ConfigError) as excinfo:
        await assert_store_supported(dsn, "duckdb")
    message = str(excinfo.value)
    assert "Embedding dimensions 1024 do not match this store's width 2560." in message
    assert "Re-migrate the store with the new width" in message


async def test_pre_meta_store_treated_as_2560(tmp_path, monkeypatch) -> None:
    """A store without a meta row passes at 2560 and gets the row backfilled."""
    monkeypatch.delenv("CORPUS_KB_EMBEDDING_DIMENSIONS", raising=False)
    path, dsn = await migrated_duck_store(tmp_path, "legacy.duckdb")
    con = duckdb.connect(str(path))
    con.execute("DELETE FROM meta WHERE key = 'embedding_width'")
    con.close()
    report = await assert_store_supported(dsn, "duckdb")
    assert report is not None
    con = duckdb.connect(str(path), read_only=True)
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'embedding_width'").fetchone()
    finally:
        con.close()
    assert row is not None and row[0] == "2560"


async def test_guard_reads_a_runner_migrated_store(tmp_path, monkeypatch) -> None:
    """Regression: the guard compares duck stores against the duck set.

    The runner records tracking rows UNQUALIFIED (main), and the two
    migration sets number independently — a duck 048 row measured against
    the PG name set (ends at 047) read as "newer" and refused in error.
    """
    monkeypatch.delenv("CORPUS_KB_EMBEDDING_DIMENSIONS", raising=False)
    _, dsn = await migrated_duck_store(tmp_path, "tracked.duckdb")
    report = await assert_store_supported(dsn, "duckdb")
    assert report is not None
    assert report.store_head == "052_source_type_backfill.sql"
    assert report.pending_count == 0
