"""Duck 047 carries pg 034's meta_embeddings CHECKs (issue #527, G04).

The DuckDB mirror 035 omitted both Postgres CHECKs, so the mirror admitted
rows Postgres rejects. DuckDB has no ALTER TABLE ADD CHECK, so 047 lands
the CHECKs through a transactional table rebuild. These tests run the file
through the real migration runner against a scratch database file.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = "tenant-1"
_DIMS = 2560
_VEC = [0.125] * _DIMS


async def _migrated(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return path


def _insert_meta(
    con: duckdb.DuckDBPyConnection,
    meta_key: str,
    meta_kind: str = "topic",
    source_count: int = 0,
) -> None:
    con.execute(
        "INSERT INTO meta_embeddings (tenant_id, meta_kind, meta_key, embedding, source_count)"
        " VALUES (?, ?, ?, ?, ?)",
        [TENANT, meta_kind, meta_key, _VEC, source_count],
    )


async def test_meta_embeddings_kind_check_enforced(tmp_path: Path) -> None:
    """A kind outside the closed vocabulary is rejected; a real one writes."""
    path = await _migrated(tmp_path, "checks-kind.duckdb")
    con = duckdb.connect(str(path))
    try:
        with pytest.raises(duckdb.ConstraintException):
            _insert_meta(con, "k-bogus", meta_kind="BOGUS")
        _insert_meta(con, "k-valid", meta_kind="profile")
        assert con.execute("SELECT count(*) FROM meta_embeddings").fetchone() == (1,)
    finally:
        con.close()


async def test_meta_embeddings_source_count_check_enforced(tmp_path: Path) -> None:
    """A negative source_count is rejected."""
    path = await _migrated(tmp_path, "checks-count.duckdb")
    con = duckdb.connect(str(path))
    try:
        with pytest.raises(duckdb.ConstraintException):
            _insert_meta(con, "k-negative", source_count=-1)
    finally:
        con.close()


async def test_existing_rows_preserved(tmp_path: Path) -> None:
    """The 047 rebuild carries every row through, values identical."""
    path = await _migrated(tmp_path, "checks-preserve.duckdb")
    con = duckdb.connect(str(path))
    try:
        _insert_meta(con, "k-a", meta_kind="topic", source_count=3)
        _insert_meta(con, "k-b", meta_kind="belief_summary", source_count=7)
        # Re-arm 047: drop only its tracking row, then seed-state snapshot.
        con.execute(
            "DELETE FROM schema_migrations WHERE filename = '047_meta_embeddings_checks.sql'"
        )
        before = con.execute(
            "SELECT meta_id, tenant_id, meta_kind, meta_key, label, source_count,"
            " built_at FROM meta_embeddings ORDER BY meta_key"
        ).fetchall()
    finally:
        con.close()

    report = await run_migrations(duckdb_path_to_conn_string(path))
    assert report.applied_now == ["047_meta_embeddings_checks.sql"]

    con = duckdb.connect(str(path))
    try:
        after = con.execute(
            "SELECT meta_id, tenant_id, meta_kind, meta_key, label, source_count,"
            " built_at FROM meta_embeddings ORDER BY meta_key"
        ).fetchall()
        assert [row[:6] for row in after] == [row[:6] for row in before]
        # TIMESTAMPTZ round-trips exactly through the rebuild.
        assert [str(row[6]) for row in after] == [str(row[6]) for row in before]
        # Both CHECKs are declared on the rebuilt table.
        checks = con.execute(
            "SELECT constraint_text FROM duckdb_constraints()"
            " WHERE table_name = 'meta_embeddings' AND constraint_type = 'CHECK'"
            " ORDER BY constraint_text"
        ).fetchall()
        texts = json.dumps([row[0] for row in checks])
        assert "meta_kind" in texts
        assert "source_count" in texts
    finally:
        con.close()
