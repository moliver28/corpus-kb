"""Behavior tests for setup preflight (issue #353).

Hard checks stop setup with fix commands; soft checks warn. No test
runs the engine CLI for real: the probes are monkeypatched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import preflight  # noqa: E402
import soft_checks  # noqa: E402


def setup_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))


# ---------------------------------------------------------------------------
# Hard checks
# ---------------------------------------------------------------------------


def test_hard_checks_fail_without_engine_and_carry_fixes(
    tmp_path: Path, monkeypatch
) -> None:
    setup_state(tmp_path, monkeypatch)
    monkeypatch.setattr(preflight.engine_paths, "engine_root", lambda root=None: None)
    monkeypatch.setattr(preflight.engine_paths, "engine_cli", lambda root=None: None)
    report = preflight.run("postgres")
    names = {row["name"] for row in report["hard"] if not row["ok"]}
    assert {"engine root", "engine venv", "engine version"} <= names
    for row in report["hard"]:
        if not row["ok"]:
            assert row["fix"], row


def test_assert_passes_raises_with_fix_lines(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    monkeypatch.setattr(preflight.engine_paths, "engine_root", lambda root=None: None)
    monkeypatch.setattr(preflight.engine_paths, "engine_cli", lambda root=None: None)
    report = preflight.run("duckdb")
    try:
        preflight.assert_passes(report)
        raised = False
    except preflight.PreflightError as exc:
        raised = True
        assert "engine root" in str(exc)
        assert "Install the engine" in str(exc)
    assert raised


def test_engine_version_comparison(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    assert preflight._version_tuple("0.9.0") < preflight._version_tuple("0.10.0")
    assert preflight._version_tuple("0.10.0") >= preflight._version_tuple("0.10.0")
    assert preflight._version_tuple("0.10.1") > preflight._version_tuple("0.10.0")
    assert preflight._version_tuple("") == (0,)


def test_duckdb_branch_checks_the_venv_import(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    root = tmp_path / "engine"
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("CORPUS_KB_REPO", str(root))
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_ENGINE_ROOT", raising=False)

    calls: list[list[str]] = []

    def fake_run(command: list[str], timeout: int = 30):
        calls.append([str(part) for part in command])
        result = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return result

    monkeypatch.setattr(preflight, "_run", fake_run)
    report = preflight.run("duckdb")
    duck = [row for row in report["hard"] if row["name"] == "duckdb"]
    assert duck and duck[0]["ok"] is True
    assert any("import duckdb" in " ".join(row) for row in calls)


def test_duckdb_missing_import_fails_hard(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    monkeypatch.setattr(preflight, "engine_venv_python", lambda root=None: None)
    report = preflight.run("duckdb")
    duck = [row for row in report["hard"] if row["name"] == "duckdb"]
    assert duck and duck[0]["ok"] is False


def test_postgres_checks_require_psql(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    report = preflight.run("postgres")
    psql = [row for row in report["hard"] if row["name"] == "psql"]
    vector = [row for row in report["hard"] if row["name"] == "pgvector"]
    assert psql and psql[0]["ok"] is False
    assert vector and vector[0]["ok"] is False


def test_pgvector_probe_uses_the_superuser_dsn(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/psql")
    seen: dict = {}

    def fake_run(command: list[str], timeout: int = 30):
        seen["command"] = [str(part) for part in command]
        return type("R", (), {"returncode": 0, "stdout": "1\n", "stderr": ""})()

    monkeypatch.setattr(preflight, "_run", fake_run)
    assert preflight.pgvector_available() is True
    assert "pg_available_extensions" in " ".join(seen["command"])


# ---------------------------------------------------------------------------
# Soft checks
# ---------------------------------------------------------------------------


def test_soft_checks_warn_but_never_stop(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    monkeypatch.setattr(
        soft_checks, "doctor_report", lambda cli: {"checks": [], "config": {}}
    )
    monkeypatch.setattr(soft_checks, "ollama_models", lambda: [])
    soft: list[dict] = []
    soft_checks.soft_checks(None, soft)
    names = [row["name"] for row in soft]
    assert names == ["Ollama", "embedding model"]
    assert all(not row["ok"] for row in soft)
    assert all(row["fix"] for row in soft)
    # Soft failures never raise.
    preflight.assert_passes({"hard": [], "soft": soft})


def test_soft_checks_read_doctor_json_and_model_list(
    tmp_path: Path, monkeypatch
) -> None:
    setup_state(tmp_path, monkeypatch)
    report = {
        "checks": [{"name": "Ollama", "level": "OK", "detail": "up"}],
        "config": {"embedding_model": "nomic-embed-text"},
    }
    monkeypatch.setattr(soft_checks, "doctor_report", lambda cli: report)
    monkeypatch.setattr(
        soft_checks, "ollama_models", lambda: ["nomic-embed-text:latest"]
    )
    soft: list[dict] = []
    soft_checks.soft_checks("corpus-kb", soft)
    assert all(row["ok"] for row in soft)
    assert soft[1]["detail"] == "nomic-embed-text"


def test_doctor_report_tolerates_bad_output(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)

    def boom(*args, **kwargs):
        raise OSError("no engine")

    monkeypatch.setattr(soft_checks, "doctor_report", boom)
    monkeypatch.setattr(soft_checks, "ollama_models", lambda: ["m"])
    soft: list[dict] = []
    soft_checks.soft_checks(None, soft)
    assert [row["name"] for row in soft] == ["Ollama", "embedding model"]
    assert json.dumps(soft)  # rows stay JSON-serializable
