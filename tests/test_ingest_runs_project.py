"""The ingest-runs project link (migration 036) and the ingest-status filter.

Offline pins for the project_id column end to end:

* ``PostgresIngestStore.set_run_project`` — statement shape per backend,
  the tenant GUC ordering (house ledger-method shape, issue #87 pin), and
  the best-effort contract (a database error is a warning, never a raise).
* ``ingest_status(project_id=...)`` — the filtered statement, the honest
  error on a pre-migration ledger or an unparsable id, and the NULL
  project read before migration 036.

DB layer is stubbed at the same seams tests/test_ingest_store_txn.py
(recording connection) and tests/test_surface_parity_deep.py (_LockConn) use.
The handler link's own pins live in tests/test_ingest_transcript_project.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from corpus_kb.tools.ingest_sql import ingest_sql_for
from corpus_kb.tools.ingest_store import PostgresIngestStore
from corpus_kb.tools.ingest_surface_tools import DEFAULT_TENANT_ID, _status_sql, ingest_status

TENANT = "00000000-0000-0000-0000-000000000003"
REPO = Path(__file__).resolve().parent.parent
MIGRATION_PG = REPO / "src" / "corpus_kb" / "migrations" / "036_ingest_runs_project.sql"
MIGRATION_DUCK = REPO / "src" / "corpus_kb" / "migrations_duckdb" / "037_ingest_runs_project.sql"


# ---------------------------------------------------------------------------
# Migration contract pins (offline, test_ingest_integrity shape)
# ---------------------------------------------------------------------------


def test_migration_036_pins_the_project_column_both_engines() -> None:
    pg = MIGRATION_PG.read_text(encoding="utf-8").lower()
    assert "alter table ingest_runs add column if not exists project_id uuid" in pg
    assert "idx_ingest_runs_tenant_project" in pg
    assert "on ingest_runs(tenant_id, project_id)" in pg
    duck = MIGRATION_DUCK.read_text(encoding="utf-8").lower()
    assert "alter table ingest_runs add column if not exists project_id varchar" in duck
    assert "idx_ingest_runs_tenant_project" in duck
    # A plain nullable column by convention: no FK (documents.project_id, 016).
    assert "references" not in pg and "references" not in duck


def test_migration_036_rollbacks_drop_the_column_and_index() -> None:
    for path in (
        REPO / "src" / "corpus_kb" / "migrations" / "rollback_036_ingest_runs_project.sql",
        REPO / "src" / "corpus_kb" / "migrations_duckdb" / "rollback_037_ingest_runs_project.sql",
    ):
        text = path.read_text(encoding="utf-8").lower()
        assert "drop index if exists idx_ingest_runs_tenant_project" in text
        assert "alter table ingest_runs drop column if exists project_id" in text


# ---------------------------------------------------------------------------
# Store: set_run_project (recording connection, test_ingest_store_txn shape)
# ---------------------------------------------------------------------------


class _RecordingConn:
    """Connection double: records SQL order; can fail one needle."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.sql: list[tuple[str, tuple[object, ...]]] = []
        self._fail_on = fail_on

    def transaction(self) -> _RecordingConn:
        return self

    async def __aenter__(self) -> _RecordingConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, sql: str, *args: object) -> str:
        if self._fail_on is not None and self._fail_on in sql:
            raise RuntimeError("db down")
        self.sql.append((sql, args))
        return "UPDATE 1"


class _RecordingPool:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def acquire(self) -> _RecordingPool:
        return self

    async def __aenter__(self) -> _RecordingConn:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_set_run_project_updates_inside_the_tenant_scope() -> None:
    conn = _RecordingConn()
    store = PostgresIngestStore(cast("Any", _RecordingPool(conn)), TENANT)
    await store.set_run_project("run-1", "proj-1")
    assert conn.sql[0][0] == "SELECT set_config('app.current_tenant_id', $1, true)"
    update_sql, update_args = conn.sql[1]
    assert "SET project_id = $2::uuid" in update_sql
    assert "WHERE run_id = $1::uuid AND tenant_id = $3" in update_sql
    assert update_args == ("run-1", "proj-1", TENANT)


@pytest.mark.asyncio
async def test_set_run_project_never_raises_on_a_database_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn = _RecordingConn(fail_on="UPDATE ingest_runs")
    store = PostgresIngestStore(cast("Any", _RecordingPool(conn)), TENANT)
    await store.set_run_project("run-1", "proj-1")  # must not raise
    assert any("Could not link ingest run" in rec.message for rec in caplog.records)


def test_set_run_project_statement_variants_per_backend() -> None:
    pg = ingest_sql_for("postgres").set_run_project_sql
    duck = ingest_sql_for("duckdb").set_run_project_sql
    assert "$1::uuid" in pg and "$2::uuid" in pg
    assert "::uuid" not in duck
    assert "WHERE run_id = $1 AND tenant_id = $3" in duck


# ---------------------------------------------------------------------------
# ingest_status: the project filter
# ---------------------------------------------------------------------------


class _StatusConn:
    """Connection double for the status read: canned probes + run rows."""

    def __init__(self, project_col: bool = True, rows: list[dict[str, Any]] | None = None) -> None:
        self.project_col = project_col
        self.rows = rows or []
        self.last_runs_sql = ""
        self.last_args: tuple[object, ...] = ()

    def transaction(self) -> _StatusConn:
        return self

    async def __aenter__(self) -> _StatusConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, sql: str, *args: object) -> str:
        return "OK"

    async def fetchval(self, sql: str, *args: object) -> Any:
        if "to_regclass" in sql:
            return "public.ingest_runs"
        if "column_name = 'project_id'" in sql:
            return 1 if self.project_col else 0
        if "column_name = 'degraded'" in sql:
            return 2
        return 0

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.last_runs_sql = sql
        self.last_args = args
        return self.rows


class _StatusPool:
    def __init__(self, conn: _StatusConn) -> None:
        self._conn = conn

    def acquire(self) -> _StatusPool:
        return self

    async def __aenter__(self) -> _StatusConn:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


def _run_row(project: str | None) -> dict[str, Any]:
    return {
        "run_id": "0f0e0d0c-0000-0000-0000-000000000001",
        "source_type": "transcript",
        "started_at": None,
        "finished_at": None,
        "status": "completed",
        "start_hash": "ab",
        "finish_hash": "cd",
        "stages": "{}",
        "error": None,
        "project_id": project,
    }


@pytest.mark.asyncio
async def test_ingest_status_filters_runs_by_project() -> None:
    project = str(uuid4())
    conn = _StatusConn(rows=[_run_row(project)])
    report = await ingest_status(
        cast("Any", _StatusPool(conn)), TENANT, limit=5, project_id=project
    )
    assert report["status"] == "success"
    assert " AND project_id = $3::uuid" in conn.last_runs_sql
    assert conn.last_args == (DEFAULT_TENANT_ID, 5, project)
    runs = report["runs"]
    assert isinstance(runs, list) and runs[0]["project_id"] == project


@pytest.mark.asyncio
async def test_ingest_status_serializes_project_null_when_unfiltered() -> None:
    conn = _StatusConn(rows=[_run_row(None)])
    report = await ingest_status(cast("Any", _StatusPool(conn)), TENANT, limit=5)
    assert "NULL AS project_id" in conn.last_runs_sql
    assert conn.last_args == (DEFAULT_TENANT_ID, 5)
    runs = report["runs"]
    assert isinstance(runs, list) and runs[0]["project_id"] is None


@pytest.mark.asyncio
async def test_ingest_status_rejects_an_unparsable_project_id() -> None:
    conn = _StatusConn()
    report = await ingest_status(cast("Any", _StatusPool(conn)), project_id="not-a-uuid")
    assert report["status"] == "error"
    assert "not-a-uuid" in str(report["message"])
    assert conn.last_runs_sql == ""  # the database was never queried


@pytest.mark.asyncio
async def test_ingest_status_errors_when_the_column_is_missing() -> None:
    conn = _StatusConn(project_col=False)
    report = await ingest_status(cast("Any", _StatusPool(conn)), project_id=str(uuid4()))
    assert report["status"] == "error"
    assert "migration 036" in str(report["message"])


def test_status_sql_duckdb_variant_binds_the_project_plain() -> None:
    duck = _status_sql(True)
    assert " AND project_id = $3" in duck["runs_project"]
    assert "::uuid" not in duck["runs_project"]
