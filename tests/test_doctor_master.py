"""The W5-6 doctor master section: skip / seeded / off-workspace rows."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from corpus_kb._setup.doctor_master import check_master, master_section
from corpus_kb._setup.doctor_research import STATUS_INFO, STATUS_SKIPPED
from corpus_kb._setup.migrate import run_migrations
from corpus_kb.master.state_store import note_activity, project_id_for_slug, resolve_lens
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import DuckDbGateway

TENANT = "00000000-0000-0000-0000-000000000001"
CFG_ON = {"master": {"workspace_root": "F:/ws"}}
CFG_OFF = {}
NOW_REAL = None


def _cleanup(path: Path) -> None:
    for suffix in ("", ".wal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


async def _migrated(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return path


async def test_master_section_skips_unmigrated_store(tmp_path) -> None:
    """An unmigrated store lacks the 048/049 tables: skipped, not dead."""
    path = tmp_path / "doctor-unmigrated.duckdb"
    gateway = await DuckDbGateway.connect(str(path))
    await gateway.close()
    section = await master_section(
        {"database": {"backend": "duckdb"}, "master": CFG_ON["master"]},
        duckdb_path_to_conn_string(path),
    )
    by_name = {c.name: c for c in section}
    assert by_name["Master workspace"].status == "ok"
    assert by_name["Master state"].status == STATUS_SKIPPED


async def test_master_checks_skip_without_db() -> None:
    """gateway None answers one skipped row, never a guess."""
    checks = await check_master(None, CFG_ON)
    by_name = {c.name: c for c in checks}
    assert by_name["Master workspace"].status == "ok"
    assert by_name["Master state"].status == STATUS_SKIPPED


async def test_master_checks_name_active_project(tmp_path: Path) -> None:
    """A seeded cache row names the slug (the SessionStart wording)."""
    from datetime import UTC, datetime, timedelta

    path = await _migrated(tmp_path, "doctor-on.duckdb")
    gateway = await DuckDbGateway.connect(str(path))
    try:
        pid = await project_id_for_slug(gateway, TENANT, "alpha")
        recent = datetime.now(UTC) - timedelta(seconds=60)
        await note_activity(gateway, TENANT, pid, "ingest", recent)
        await resolve_lens(
            gateway,
            {"master": {"workspace_root": "F:/ws", "idle_window_s": 7200}},
            TENANT,
            datetime.now(UTC),
        )
        checks = await check_master(gateway, CFG_ON)
        by_name = {c.name: c for c in checks}
        assert by_name["Master workspace"].status == "ok"
        assert by_name["Master state"].status == "ok"
        assert "Active project: alpha." in by_name["Master state"].detail
    finally:
        await gateway.close()
        _cleanup(path)


async def test_master_checks_off_workspace(tmp_path: Path) -> None:
    """Empty workspace_root: an info row, never a warn, with no DB needed."""
    checks = await check_master(None, CFG_OFF)
    statuses = [c.status for c in checks]
    assert STATUS_INFO in statuses
    assert "warn" not in statuses
    assert "fail" not in statuses
