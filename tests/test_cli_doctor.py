"""The ``corpus-kb doctor`` CLI wrapper (issue #348).

The wrapper forwards ``--json`` to the installer's doctor and raises the
honest exit: 3 when the configured database is unreachable, 0 when it
answers. Offline by construction: the probes are faked at the
install-module seams, so no Postgres or Ollama is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb._setup import doctor_storage, install
from corpus_kb.cli import app

runner = CliRunner()


@pytest.fixture
def offline(monkeypatch: Any, tmp_path: Path) -> None:
    """Clean env, offline probes, a repo-root config.yaml with a DSN.

    The installer's loader is config-first (no env overrides), so the
    wrapper's probe target comes from the written file, not the env.
    """
    monkeypatch.chdir(tmp_path)
    for name in (
        "CORPUS_KB_DATABASE_URL",
        "CORPUS_KB_DATABASE_BACKEND",
        "CORPUS_KB_CONFIG",
        "CORPUS_KB_CONFIG_OVERLAY",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config.yaml").write_text(
        "database:\n"
        "  backend: postgres\n"
        "  connection_string:"
        " postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb\n",
        encoding="utf-8",
    )

    async def down_postgres(conn_str: str) -> tuple[bool, str]:
        return False, "connection refused"

    async def down_ollama(base_url: str) -> tuple[bool, str]:
        return False, "status 000"

    async def no_storage(config: dict[str, Any], conn_str: str, postgres_ok: bool) -> list[Any]:
        return []

    monkeypatch.setattr(install, "check_postgres", down_postgres)
    monkeypatch.setattr(install, "check_ollama", down_ollama)
    monkeypatch.setattr(doctor_storage, "storage_checks", no_storage)


def test_cli_doctor_exits_3_when_database_down(offline: None) -> None:
    """The wrapper raises the doctor's exit 3 instead of returning it."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3, result.output


def test_cli_doctor_json_exits_0_when_database_up(offline: None, monkeypatch: Any) -> None:
    """``doctor --json`` prints the machine report and keeps exit 0."""

    async def up_postgres(conn_str: str) -> tuple[bool, str]:
        return True, "PostgreSQL 16.4"

    monkeypatch.setattr(install, "check_postgres", up_postgres)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    levels = {check["name"]: check["level"] for check in report["checks"]}
    assert levels["Postgres"] == "OK"
