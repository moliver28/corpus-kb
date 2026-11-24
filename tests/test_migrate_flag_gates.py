"""The migrate Postgres-only flags are typed-refused on a DuckDB target.

Issue #527 (G07): ``--superuser-dsn``, ``--work-mem``, and
``--reset-embeddings`` used to be silent no-ops under a ``duckdb:///``
target. The refusal paths raise before any connection, so no database is
needed here; the Postgres acceptance leg rides a runner double.
"""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from corpus_kb._setup.migrate_report import MigrateReport
from corpus_kb.cli import app

runner = CliRunner()


def _duck_target(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", "duckdb:///" + (tmp_path / "g.duckdb").as_posix())
    monkeypatch.chdir(tmp_path)  # keep the repo config.yaml out of discovery


def _assert_refused(result: Any, flag: str) -> None:
    assert result.exit_code == 3, result.output
    assert flag in result.output
    assert "Postgres" in result.output
    assert "DuckDB" in result.output


def test_migrate_duck_refuses_superuser_dsn(tmp_path: Any, monkeypatch: Any) -> None:
    _duck_target(monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["migrate", "--dry-run", "--superuser-dsn", "postgresql://su@localhost/db"]
    )
    _assert_refused(result, "--superuser-dsn")


def test_migrate_duck_refuses_work_mem(tmp_path: Any, monkeypatch: Any) -> None:
    _duck_target(monkeypatch, tmp_path)
    result = runner.invoke(app, ["migrate", "--dry-run", "--work-mem", "1GB"])
    _assert_refused(result, "--work-mem")


def test_migrate_duck_refuses_reset_embeddings(tmp_path: Any, monkeypatch: Any) -> None:
    _duck_target(monkeypatch, tmp_path)
    result = runner.invoke(app, ["migrate", "--dry-run", "--reset-embeddings"])
    _assert_refused(result, "--reset-embeddings")


def test_migrate_pg_accepts_all_flags(tmp_path: Any, monkeypatch: Any) -> None:
    """Every flag rides through to the runner on a Postgres target."""
    import corpus_kb._setup.migrate as migrate_mod

    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", "postgresql://app@localhost/corpus_kb")
    monkeypatch.chdir(tmp_path)

    seen: dict[str, Any] = {}

    async def fake_run(conn_str: str, **kwargs: Any) -> MigrateReport:
        seen["conn_str"] = conn_str
        seen.update(kwargs)
        return MigrateReport("postgres", [], ["046_embedding_cache.sql"], [], True)

    monkeypatch.setattr(migrate_mod, "run_migrations", fake_run)
    result = runner.invoke(
        app,
        [
            "migrate",
            "--dry-run",
            "--superuser-dsn",
            "postgresql://su@localhost/db",
            "--work-mem",
            "1GB",
            "--maintenance-work-mem",
            "2GB",
            "--reset-embeddings",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["conn_str"] == "postgresql://app@localhost/corpus_kb"
    assert seen["superuser_dsn"] == "postgresql://su@localhost/db"
    assert seen["session_settings"] == {"work_mem": "1GB", "maintenance_work_mem": "2GB"}
    assert seen["reset_embeddings"] is True
    assert seen["dry_run"] is True


def test_flag_help_text_says_postgres_only(tmp_path: Any, monkeypatch: Any) -> None:
    """Each Postgres-only option help names the backend, so --help is honest."""
    _duck_target(monkeypatch, tmp_path)
    result = runner.invoke(app, ["migrate", "--help"])
    assert result.exit_code == 0, result.output
    help_text = json.dumps(result.output)
    assert help_text.count("Postgres only.") >= 4  # all four pg-only flags
