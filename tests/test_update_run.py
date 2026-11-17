"""The ``corpus-kb update`` orchestration (W5-a).

Execution honesty is the contract under test:

- ``--dry-run`` reads the store state and prints the plan; it applies
  nothing and runs no upgrade step.
- A real run checks the store version FIRST (a newer store refuses with
  the typed error), then applies pending migrations through the shared
  runner, then verifies the store against the code head.
- An unreachable database is the typed database-unavailable failure.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb._setup.migrate_report import MigrateReport
from corpus_kb._setup.update_flavor import InstallFlavor
from corpus_kb._setup.update_run import UpdateResult, run_update
from corpus_kb._setup.version_guard import StoreVersionReport
from corpus_kb.errors import (
    DatabaseUnavailableError,
    PackageNotPublishedError,
    StoreNewerError,
)


class _RecordingRunner:
    """The migration-runner double: records each call's dry_run flag."""

    def __init__(self, pending: list[str] | None = None) -> None:
        self.pending = pending if pending is not None else ["046_next.sql"]
        self.calls: list[bool] = []

    async def __call__(self, _conn: str, *, dry_run: bool = False, **_kw: Any) -> MigrateReport:
        self.calls.append(dry_run)
        applied = [] if dry_run else list(self.pending)
        return MigrateReport("postgres", ["001_a.sql"], list(self.pending), applied, dry_run)


class _RecordingGuard:
    """The store-version guard double: records calls, answers a pass."""

    def __init__(self, pending_count: int = 0) -> None:
        self.calls: list[tuple[str, str]] = []
        self._pending_count = pending_count

    async def __call__(self, conn: str, backend: str) -> StoreVersionReport:
        self.calls.append((conn, backend))
        return StoreVersionReport(
            store_head="001_a.sql",
            code_head="046_next.sql",
            pending_count=self._pending_count,
            fresh=False,
        )


async def test_dry_run_applies_nothing() -> None:
    runner = _RecordingRunner()
    guard = _RecordingGuard()
    result = await run_update(
        "postgresql://stub",
        InstallFlavor.EDITABLE,
        dry_run=True,
        migration_runner=runner,
        store_checker=guard,
    )
    assert runner.calls == [True], "the dry run may read, never apply"
    assert result.dry_run is True
    assert result.migration_report is not None
    assert result.migration_report.applied_now == []


async def test_dry_run_reports_whether_the_store_is_current() -> None:
    behind = await run_update(
        "postgresql://stub",
        InstallFlavor.EDITABLE,
        dry_run=True,
        migration_runner=_RecordingRunner(pending=["046_next.sql"]),
        store_checker=_RecordingGuard(pending_count=1),
    )
    assert behind.schema_current is False
    current = await run_update(
        "postgresql://stub",
        InstallFlavor.EDITABLE,
        dry_run=True,
        migration_runner=_RecordingRunner(pending=[]),
        store_checker=_RecordingGuard(pending_count=0),
    )
    assert current.schema_current is True


async def test_real_run_applies_then_verifies() -> None:
    runner = _RecordingRunner()
    guard = _RecordingGuard()
    result = await run_update(
        "postgresql://stub",
        InstallFlavor.EDITABLE,
        migration_runner=runner,
        store_checker=guard,
    )
    assert runner.calls == [False], "the real run applies once"
    assert result.dry_run is False
    assert result.schema_current is True
    assert result.migration_report is not None
    assert result.migration_report.applied_now == ["046_next.sql"]
    # The guard runs before AND after the apply: refuse, then verify.
    assert len(guard.calls) == 2


async def test_newer_store_refuses_before_any_apply() -> None:
    """The boot-guard contract holds on the update path too."""

    async def refuse(_conn: str, _backend: str) -> StoreVersionReport:
        raise StoreNewerError("The store is newer than this code.")

    runner = _RecordingRunner()
    with pytest.raises(StoreNewerError):
        await run_update(
            "postgresql://stub",
            InstallFlavor.EDITABLE,
            migration_runner=runner,
            store_checker=refuse,
        )
    assert runner.calls == [], "a refused store must not migrate"


async def test_unreachable_database_is_the_typed_failure() -> None:
    async def down(_conn: str, *, dry_run: bool = False, **_kw: Any) -> MigrateReport:
        raise ConnectionError("connection refused")

    with pytest.raises(DatabaseUnavailableError):
        await run_update(
            "postgresql://stub",
            InstallFlavor.EDITABLE,
            migration_runner=down,
            store_checker=_RecordingGuard(),
        )


async def test_result_carries_the_plan_for_the_printer() -> None:
    result: UpdateResult = await run_update(
        "postgresql://stub",
        InstallFlavor.PIPX,
        dry_run=True,
        published_probe=lambda: True,
        migration_runner=_RecordingRunner(),
        store_checker=_RecordingGuard(),
    )
    assert result.plan.flavor is InstallFlavor.PIPX
    assert result.plan.to_dict()["flavor"] == "pipx"


async def test_unpublished_probe_raises_the_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #463 honesty: a PyPI 404 refuses the pip flavor, typed.

    The live endpoint answers 404 today (observed on the owner machine
    during the W5-a live proof); the mock pins that contract hermetically.
    """

    class _NotFoundResponse:
        status_code = 404

    monkeypatch.setattr("httpx.get", lambda *_a, **_kw: _NotFoundResponse())
    with pytest.raises(PackageNotPublishedError):
        await run_update(
            "postgresql://stub",
            InstallFlavor.PIP,
            dry_run=True,
            migration_runner=_RecordingRunner(),
            store_checker=_RecordingGuard(),
        )


async def test_duckdb_dsn_checks_the_duckdb_store(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def spy(conn: str, backend: str) -> StoreVersionReport:
        seen.append(backend)
        return StoreVersionReport(
            store_head="001_a.sql", code_head="046_next.sql", pending_count=1, fresh=False
        )

    runner = _RecordingRunner()
    await run_update(
        "duckdb:///C:/data/x.duckdb",
        InstallFlavor.EDITABLE,
        dry_run=True,
        migration_runner=runner,
        store_checker=spy,
    )
    assert seen == ["duckdb"]
