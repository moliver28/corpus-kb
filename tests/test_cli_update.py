"""``corpus-kb update`` CLI adapter legs (W5-a).

The command is a thin adapter over ``_setup/update_run`` (one handler,
one CLI surface, no registry op — the recorded lifecycle exemption like
``migrate``). These legs pin the honest surfaces: the registered help
line, the dry-run report, the JSON payload, and the typed-error exit
codes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb._setup.migrate_report import MigrateReport
from corpus_kb._setup.update_flavor import InstallFlavor
from corpus_kb.cli import app
from corpus_kb.errors import PackageNotPublishedError, StoreNewerError

runner = CliRunner()


def _duckdb_dsn(tmp_path: Path) -> str:
    return "duckdb:///" + (tmp_path / "scratch.duckdb").as_posix()


def _stub_flavor(monkeypatch: pytest.MonkeyPatch, flavor: InstallFlavor) -> None:
    import corpus_kb._setup.update_flavor as flavor_mod

    monkeypatch.setattr(flavor_mod, "detect_flavor", lambda **_kw: flavor)


def _stub_probe(monkeypatch: pytest.MonkeyPatch, answer: bool) -> None:
    """The PyPI probe binding run_update actually resolves at call time."""
    import corpus_kb._setup.update_run as run_mod

    monkeypatch.setattr(run_mod, "probe_pypi_published", lambda *_a, **_kw: answer)


def _stub_run_update(monkeypatch: pytest.MonkeyPatch, replacer: Any) -> None:
    import corpus_kb._setup.update_run as run_mod

    monkeypatch.setattr(run_mod, "run_update", replacer)


def test_update_command_is_registered() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "update" in result.output


def test_dry_run_prints_the_plan_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real duckdb scratch: the dry run reads a fresh store, applies nothing."""
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)
    _stub_flavor(monkeypatch, InstallFlavor.EDITABLE)
    result = runner.invoke(app, ["update", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Flavor: editable" in result.output
    assert "git pull --ff-only" in result.output
    assert "Dry run." in result.output
    assert "Restart:" in result.output


def test_json_payload_has_plan_and_migration_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)
    _stub_flavor(monkeypatch, InstallFlavor.PIPX)
    _stub_probe(monkeypatch, True)
    result = runner.invoke(app, ["update", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["flavor"] == "pipx"
    assert payload["dry_run"] is True
    # A fresh scratch file is behind the code, and the dry run says so.
    assert payload["schema_current"] is False
    assert payload["migrations"]["pending"]
    assert payload["plan"]["restart"]
    assert payload["migrations"]["backend"] == "duckdb"


def test_real_run_reports_the_restart_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)
    _stub_flavor(monkeypatch, InstallFlavor.EDITABLE)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0, result.output
    assert "Applied now:" in result.output
    assert "Store schema:" in result.output
    assert "Restart:" in result.output


def test_store_newer_error_maps_to_exit_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)
    _stub_flavor(monkeypatch, InstallFlavor.EDITABLE)

    async def refuse(*_a: Any, **_kw: Any) -> Any:
        raise StoreNewerError("The store has migration 999_future.sql.")

    _stub_run_update(monkeypatch, refuse)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 3, result.output
    assert "ERROR:" in result.output
    assert "999_future.sql" in result.output


def test_unpublished_package_maps_to_exit_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)
    _stub_flavor(monkeypatch, InstallFlavor.PIP)

    async def refuse(*_a: Any, **_kw: Any) -> MigrateReport:
        raise PackageNotPublishedError("corpus-kb is not on PyPI yet.")

    _stub_run_update(monkeypatch, refuse)
    result = runner.invoke(app, ["update", "--dry-run"])
    assert result.exit_code == 3, result.output
    assert "PyPI" in result.output


def test_without_any_database_exits_three(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    import corpus_kb.config as config_mod

    monkeypatch.setattr(config_mod, "load_config", lambda: {"database": {}})
    result = runner.invoke(app, ["update", "--dry-run"])
    assert result.exit_code == 3
    assert "No database found" in result.output
