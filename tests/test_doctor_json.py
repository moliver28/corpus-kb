"""Issue #348 scope 4: doctor exit 3 and ``doctor --json``.

Exit 3 means "the configured database is unreachable" (the same meaning
exit 3 has for ``ask``); warnings keep exit 0. The JSON report parses
with ``json.loads`` and carries name/level/fix per check plus the
resolved config values a client needs.

Offline by construction: the probes are faked at the install-module
seams, so no Postgres or Ollama is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from corpus_kb._setup import doctor_storage, install
from corpus_kb._setup.doctor_engine import database_reachable
from corpus_kb._setup.doctor_research import ResearchCheck
from corpus_kb.config import get_default_config


@pytest.fixture
def isolated(monkeypatch: Any, tmp_path: Path) -> dict[str, Any]:
    """Clean env, offline probes, a postgres-backed config."""
    monkeypatch.chdir(tmp_path)
    for name in (
        "CORPUS_KB_DATABASE_URL",
        "CORPUS_KB_DATABASE_BACKEND",
        "CORPUS_KB_CONFIG",
        "CORPUS_KB_CONFIG_OVERLAY",
    ):
        monkeypatch.delenv(name, raising=False)

    async def fake_postgres(conn_str: str) -> tuple[bool, str]:
        return False, "connection refused"

    async def fake_ollama(base_url: str) -> tuple[bool, str]:
        return False, "status 000"

    async def fake_storage(
        config: dict[str, Any], conn_str: str, postgres_ok: bool
    ) -> list[ResearchCheck]:
        return []

    monkeypatch.setattr(install, "check_postgres", fake_postgres)
    monkeypatch.setattr(install, "check_ollama", fake_ollama)
    monkeypatch.setattr(doctor_storage, "storage_checks", fake_storage)

    config = get_default_config()
    config["database"]["connection_string"] = (
        "postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb"
    )
    return {"config": config, "tmp_path": tmp_path}


async def test_doctor_exits_3_when_database_unreachable(isolated: dict[str, Any]) -> None:
    """The issue Check: with the database down, doctor exits 3."""
    assert await install.doctor_cmd(isolated["config"]) == 3


async def test_doctor_exit_stays_0_when_database_answers(
    isolated: dict[str, Any], monkeypatch: Any
) -> None:
    """A reachable database means exit 0, even with warnings (Ollama down)."""

    async def up_postgres(conn_str: str) -> tuple[bool, str]:
        return True, "PostgreSQL 16.4"

    monkeypatch.setattr(install, "check_postgres", up_postgres)
    assert await install.doctor_cmd(isolated["config"]) == 0


async def test_doctor_json_parses(isolated: dict[str, Any], monkeypatch: Any, capsys: Any) -> None:
    """The issue Check: ``doctor --json`` parses with json.loads."""
    overlay = isolated["tmp_path"] / "study-overlay.yaml"
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", str(overlay))
    code = await install.doctor_cmd(isolated["config"], json_output=True)
    report = json.loads(capsys.readouterr().out)
    assert code == 3  # the exit code survives --json

    names = {check["name"] for check in report["checks"]}
    assert {"Postgres", "Ollama"} <= names
    postgres = next(c for c in report["checks"] if c["name"] == "Postgres")
    assert postgres["level"] == "FAIL"
    assert postgres["fix"]
    ollama = next(c for c in report["checks"] if c["name"] == "Ollama")
    assert ollama["level"] == "WARN"  # a warning, never the exit code

    resolved = report["config"]
    assert resolved["backend"] == "postgres"
    assert resolved["database"] == "corpus_kb"
    assert resolved["embedding_model"]
    assert resolved["chat_model"]
    assert resolved["config_overlays"] == [str(overlay)]


async def test_doctor_json_levels_are_uppercase(
    isolated: dict[str, Any], monkeypatch: Any, capsys: Any
) -> None:
    """ResearchCheck statuses surface as OK/INFO/WARN/FAIL."""

    async def fake_storage(
        config: dict[str, Any], conn_str: str, postgres_ok: bool
    ) -> list[ResearchCheck]:
        return [
            ResearchCheck(name="DuckDB file lock", status="ok", detail="free"),
            ResearchCheck(name="Engine fit", status="warn", detail="big corpus"),
            ResearchCheck(name="FTS watermark", status="info", detail="fresh"),
        ]

    monkeypatch.setattr(doctor_storage, "storage_checks", fake_storage)
    await install.doctor_cmd(isolated["config"], json_output=True)
    report = json.loads(capsys.readouterr().out)
    levels = {c["name"]: c["level"] for c in report["checks"]}
    assert levels["DuckDB file lock"] == "OK"
    assert levels["Engine fit"] == "WARN"
    assert levels["FTS watermark"] == "INFO"


async def test_doctor_text_reports_overlays(
    isolated: dict[str, Any], monkeypatch: Any, capsys: Any
) -> None:
    """The text report names the overlay paths (issue #348 scope 2)."""
    overlay = isolated["tmp_path"] / "study-overlay.yaml"
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", str(overlay))
    await install.doctor_cmd(isolated["config"])
    out = capsys.readouterr().out
    assert "Overlays:" in out
    assert str(overlay) in out


class TestDatabaseReachable:
    """The exit-rule helper: only a real database failure flips to 3."""

    def test_postgres_follows_the_probe(self) -> None:
        assert database_reachable("postgres", False, []) is False
        assert database_reachable("postgres", True, []) is True

    def test_duckdb_lock_ok_is_reachable(self) -> None:
        checks = [ResearchCheck(name="DuckDB file lock", status="ok", detail="free")]
        assert database_reachable("duckdb", False, checks) is True

    def test_duckdb_lock_fail_is_unreachable(self) -> None:
        checks = [ResearchCheck(name="DuckDB file lock", status="fail", detail="locked")]
        assert database_reachable("duckdb", False, checks) is False

    def test_duckdb_missing_file_warn_keeps_exit_0(self) -> None:
        checks = [ResearchCheck(name="DuckDB file lock", status="warn", detail="no file")]
        assert database_reachable("duckdb", False, checks) is True

    def test_duckdb_unrelated_fail_keeps_exit_0(self) -> None:
        checks = [ResearchCheck(name="SQLite event store", status="fail", detail="broken")]
        assert database_reachable("duckdb", False, checks) is True

    def test_duckdb_config_pair_fail_is_unreachable(self) -> None:
        checks = [ResearchCheck(name="database config pair", status="fail", detail="bad")]
        assert database_reachable("duckdb", False, checks) is False
