"""CLI contract for the project-table op commands (Track C2).

Offline by construction: the config/pool seam (``cli_ingest``) and the
shared handlers (``tools.project_tools``) are faked, so the exit codes,
the human lines, the JSON shape, and the typed-error path are pinned
without a database.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.errors import NotFoundError
from tests.cli_fakes import FakePool

runner = CliRunner()

TENANT = "20000000-0000-0000-0000-000000000002"


def _patch_world(monkeypatch: pytest.MonkeyPatch) -> FakePool:
    pool = FakePool()

    async def _open(cfg: dict[str, object]) -> FakePool:
        return pool

    monkeypatch.setattr("corpus_kb.cli.cli_ingest._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli.cli_ingest._load_config", lambda: {"database": {}})
    return pool


def _spy(seen: dict[str, Any], payload: dict[str, Any]) -> Any:
    async def handler(pool: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return dict(payload)

    return handler


def test_project_provision_prints_the_human_lines_and_forwards_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _patch_world(monkeypatch)
    seen: dict[str, Any] = {}
    payload = {
        "status": "success",
        "table": "raw_meeting_notes",
        "registry_id": "r-1",
        "backend": "duckdb",
        "already_provisioned": False,
    }
    monkeypatch.setattr("corpus_kb.tools.project_tools.project_provision", _spy(seen, payload))
    result = runner.invoke(
        app,
        ["project-provision", "meeting-notes", "--project-id", "p-1", "--tenant-id", TENANT],
    )
    assert result.exit_code == 0, result.output
    assert seen == {"slug": "meeting-notes", "project_id": "p-1", "tenant_id": TENANT}
    assert "project: success" in result.output
    assert "table: raw_meeting_notes" in result.output
    assert "backend: duckdb" in result.output
    assert pool.closed


def test_project_list_json_is_the_handler_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_world(monkeypatch)
    seen: dict[str, Any] = {}
    payload = {
        "status": "success",
        "entries": [{"table": "raw_alpha", "row_count": 3}],
        "count": 1,
    }
    monkeypatch.setattr("corpus_kb.tools.project_tools.project_list", _spy(seen, payload))
    result = runner.invoke(app, ["project-list", "--json", "--tenant-id", TENANT])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload
    assert seen == {"tenant_id": TENANT}


def test_project_list_human_lines_name_each_table(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_world(monkeypatch)
    payload = {
        "status": "success",
        "entries": [{"table": "raw_alpha", "row_count": 3}, "not-a-dict"],
        "count": 2,
    }
    monkeypatch.setattr("corpus_kb.tools.project_tools.project_list", _spy({}, payload))
    result = runner.invoke(app, ["project-list"])
    assert result.exit_code == 0, result.output
    assert "count: 2" in result.output
    assert "- raw_alpha rows=3" in result.output


def test_project_drop_reports_dropped_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_world(monkeypatch)
    seen: dict[str, Any] = {}
    payload = {"status": "success", "table": "raw_alpha", "dropped_rows": 4}
    monkeypatch.setattr("corpus_kb.tools.project_tools.project_drop", _spy(seen, payload))
    result = runner.invoke(app, ["project-drop", "alpha"])
    assert result.exit_code == 0, result.output
    assert seen["slug"] == "alpha"
    assert "dropped_rows: 4" in result.output


def test_project_drop_typed_error_exits_one_with_remediation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _patch_world(monkeypatch)

    async def boom(pool: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotFoundError("No raw table for slug 'ghost'.", remediation="Run project-list.")

    monkeypatch.setattr("corpus_kb.tools.project_tools.project_drop", boom)
    result = runner.invoke(app, ["project-drop", "ghost"])
    assert result.exit_code == 1
    assert "ERROR: No raw table for slug 'ghost'." in result.output
    assert "Run project-list." in result.output
    assert pool.closed
