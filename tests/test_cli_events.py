"""The ``corpus-kb events`` command (issue #350) against a real DuckDB file.

The command is one thin adapter over ``tools.events_tools.events_summary``.
The tests pin both output shapes (the short human view and the raw
``--json`` envelope) and the honest exit codes on a migrated scratch
file, offline by construction.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app

runner = CliRunner()

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture()
def ops_dsn(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    """One migrated DuckDB file; the env DSN points at it."""
    from corpus_kb._setup.migrate import run_duckdb_migrations

    dsn = "duckdb:///" + (tmp_path / "events-cli.duckdb").as_posix()
    run_duckdb_migrations(dsn)
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", dsn)
    monkeypatch.delenv("CORPUS_KB_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)  # keep the repo config.yaml out of discovery
    return dsn


def test_events_cli_human_summary(ops_dsn: str) -> None:
    """The default view is the short summary: counts only, exit 0."""
    result = runner.invoke(app, ["events"])
    assert result.exit_code == 0, result.output
    view = dict(json.loads(result.output))
    assert view["status"] == "success"
    assert view["events_store"] == "sqlite"
    assert view["latest_notification_id"] == 0
    assert view["events_by_topic"] == {}
    assert view["dead_letter_count"] == 0
    assert view["checkpoint_count"] == 0
    assert view["recent_run_count"] == 0


def test_events_cli_json_flag_prints_full_envelope(ops_dsn: str) -> None:
    """``--json`` prints the raw tool envelope with the full blocks."""
    result = runner.invoke(app, ["events", "--json"])
    assert result.exit_code == 0, result.output
    envelope = dict(json.loads(result.output))
    assert envelope["status"] == "success"
    assert envelope["checkpoints"] == []
    assert envelope["dead_letters"]["count"] == 0
    assert envelope["recent_runs"] == []
