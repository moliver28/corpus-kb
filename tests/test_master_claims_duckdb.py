"""Master-membership tables (W4): project_claims, project_activity, master_state.

These tests run the DuckDB migration set against a scratch database and
pin the contracts the claim branch and the active-state module build
on: the three tables exist, and the migration backfill seeds one origin
claim per documents row that carries a project_id.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = "tenant-1"


async def _migrated(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return path


async def test_membership_tables_exist(tmp_path: Path) -> None:
    """A SELECT from each membership table answers zero rows, not an error."""
    path = await _migrated(tmp_path, "membership.duckdb")
    con = duckdb.connect(str(path))
    try:
        for table in ("project_claims", "project_activity", "master_state"):
            rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert rows == (0,), table
    finally:
        con.close()


async def test_backfill_seeds_origin_claims(tmp_path: Path) -> None:
    """Re-running the migration seeds one claim per origin membership row."""
    path = await _migrated(tmp_path, "backfill.duckdb")
    project_id = "44444444-4444-4444-4444-444444444444"
    doc_id = "00000000-0000-0000-0000-0000000000d1"
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, project_id) VALUES (?, ?, ?, ?)",
            [doc_id, TENANT, "F:/seed/x.md", project_id],
        )
        # Re-arm the membership migration: drop only its tracking row.
        con.execute("DELETE FROM schema_migrations WHERE filename = '049_master_membership.sql'")
    finally:
        con.close()
    report = await run_migrations(duckdb_path_to_conn_string(path))
    assert report.applied_now == ["049_master_membership.sql"]
    con = duckdb.connect(str(path))
    try:
        rows = con.execute(
            "SELECT project_id, doc_id, source_project_id FROM project_claims WHERE tenant_id = ?",
            [TENANT],
        ).fetchall()
        assert rows == [(project_id, doc_id, project_id)]
    finally:
        con.close()
