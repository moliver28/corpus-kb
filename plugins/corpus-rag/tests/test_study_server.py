"""Behavior tests for the per-study server manager (issue #351).

Covers: use marks, the idle reaper (stops idle servers, never the
current one), health waiting, DuckDB stop-before ordering, and the start
environment. No test starts a real server.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import study_registry  # noqa: E402
import study_server  # noqa: E402


@pytest.fixture(autouse=True)
def plugin_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(tmp_path / "engine-state"))
    return tmp_path


def _registry_with(slug: str, port: int = 8010, backend: str = "postgres") -> dict:
    registry = study_registry.empty_registry()
    study_registry.register_study(registry, slug, f"postgresql://x/{slug}", port, backend)
    return registry


def test_use_mark_written_and_aged() -> None:
    assert study_server.last_used_age("alpha") is None
    study_server.note_use("alpha")
    age = study_server.last_used_age("alpha")
    assert age is not None and age < 5


def test_reaper_stops_idle_and_never_current(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry_with("alpha")
    study_registry.register_study(registry, "beta", "postgresql://x/beta", 8011, "postgres")
    study_registry.register_study(registry, "gamma", "postgresql://x/gamma", 8012, "postgres")
    old = time.time() - (study_server.IDLE_LIMIT_SECONDS + 60)
    study_server.note_use("beta")
    os.utime(study_server.last_used_path("beta"), (old, old))
    study_server.note_use("gamma")  # fresh
    stopped: list[str] = []
    monkeypatch.setattr(
        study_server, "stop_study", lambda slug, study, grace=30: stopped.append(slug) or True
    )
    result = study_server.reap_idle(registry, current_slug="alpha")
    assert result == ["beta"]
    assert stopped == ["beta"]


def test_reaper_skips_never_used_and_dead_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry_with("alpha")
    study_registry.register_study(registry, "beta", "postgresql://x/beta", 8011, "postgres")
    stopped: list[str] = []
    monkeypatch.setattr(
        study_server, "stop_study", lambda slug, study, grace=30: stopped.append(slug) or True
    )
    assert study_server.reap_idle(registry, current_slug="alpha") == []
    assert stopped == []


def test_wait_healthy_true_when_probe_answers() -> None:
    assert study_server.wait_healthy(8010, None, 5, sleep=lambda s: None, probe_fn=lambda p: True)


def test_wait_healthy_fails_fast_when_process_exits() -> None:
    slept: list[float] = []
    ok = study_server.wait_healthy(
        8010, 4242, 60, sleep=slept.append, probe_fn=lambda p: False, alive_fn=lambda pid: False
    )
    assert ok is False
    assert slept == []  # no waiting after the process died


def test_wait_healthy_times_out() -> None:
    ok = study_server.wait_healthy(
        8010, None, 0, sleep=lambda s: None, probe_fn=lambda p: False
    )
    assert ok is False


def test_as_pid_rejects_junk() -> None:
    assert study_server.as_pid(None) is None
    assert study_server.as_pid(0) is None
    assert study_server.as_pid(-3) is None
    assert study_server.as_pid("words") is None
    assert study_server.as_pid(4242) == 4242
    assert study_server.as_pid("4242") == 4242


def test_stop_before_only_touches_duckdb(monkeypatch: pytest.MonkeyPatch) -> None:
    postgres = {"backend": "postgres", "pid": 99}
    assert study_server.stop_before("alpha", postgres, "migrate") is False
    duck = {"backend": "duckdb", "pid": 99}
    killed: list[int] = []
    monkeypatch.setattr(study_server, "pid_alive", lambda pid: True)
    monkeypatch.setattr(study_server, "terminate_pid", lambda pid, grace: killed.append(pid))
    assert study_server.stop_before("alpha", duck, "migrate") is True
    assert killed == [99]
    assert duck["pid"] is None


def test_stop_before_idle_duckdb_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    duck = {"backend": "duckdb", "pid": None}
    assert study_server.stop_before("alpha", duck, "backup") is False


def test_ensure_running_reuses_healthy_server(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry_with("alpha")
    study = registry["studies"]["alpha"]
    study["pid"] = 99
    monkeypatch.setattr(study_server, "pid_alive", lambda pid: True)
    monkeypatch.setattr(study_server, "probe", lambda port, timeout=5.0: True)
    monkeypatch.setattr(
        study_registry, "save_registry",
        lambda reg: (_ for _ in ()).throw(AssertionError("no save expected")),
    )
    assert study_server.ensure_running("corpus-kb", registry, "alpha", study) is study


def test_ensure_running_starts_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry_with("alpha")
    study = registry["studies"]["alpha"]
    study["pid"] = None
    calls: dict = {}

    def fake_start(cli, slug, row, wait=None):
        calls.update(cli=cli, slug=slug, port=row["port"])
        return 4242

    monkeypatch.setattr(study_server, "start", fake_start)
    monkeypatch.setattr(study_registry, "save_registry", lambda reg: calls.update(saved=True))
    out = study_server.ensure_running("corpus-kb", registry, "alpha", study)
    assert out["pid"] == 4242
    assert calls["cli"] == "corpus-kb" and calls["slug"] == "alpha" and calls["port"] == 8010
    assert calls["saved"] is True


def test_ensure_running_moves_off_a_busy_port(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry_with("alpha")
    study = registry["studies"]["alpha"]
    study["pid"] = None
    monkeypatch.setattr(study_registry, "port_free", lambda port: False)
    monkeypatch.setattr(study_registry, "allocate_port", lambda reg, base=None: 8013)

    def fake_start(cli, slug, row, wait=None):
        assert row["port"] == 8013
        return 4242

    monkeypatch.setattr(study_server, "start", fake_start)
    monkeypatch.setattr(study_registry, "save_registry", lambda reg: None)
    study_server.ensure_running("corpus-kb", registry, "alpha", study)
    assert study["port"] == 8013


def test_start_builds_detached_command_with_study_env(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry_with("alpha")
    study = registry["studies"]["alpha"]
    seen: dict = {}

    class FakeProcess:
        pid = 4242

    def fake_popen(command, stdout=None, stderr=None, stdin=None, env=None, **kwargs):
        seen.update(command=command, env=env, kwargs=kwargs)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(study_server, "wait_healthy", lambda port, pid, timeout, **kw: True)
    pid = study_server.start("corpus-kb", "alpha", study, wait=1)
    assert pid == 4242
    assert seen["command"][:5] == ["corpus-kb", "start", "--transport", "http", "--port"]
    assert "--no-ui" in seen["command"] and "8010" in seen["command"]
    assert seen["env"]["CORPUS_KB_DATABASE_URL"] == "postgresql://x/alpha"
    assert seen["env"]["CORPUS_KB_DATABASE_BACKEND"] == "postgres"
    assert seen["env"]["CORPUS_KB_SOCKET_PATH"].endswith("alpha.sock")
    log = study_server.logs_dir() / "alpha.log"
    assert log.parent.name == "logs"


def test_start_error_names_log_path(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry_with("alpha")
    study = registry["studies"]["alpha"]

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **kw: FakeProcess()
    )
    monkeypatch.setattr(study_server, "wait_healthy", lambda port, pid, timeout, **kw: False)
    monkeypatch.setattr(study_server, "pid_alive", lambda pid: False)
    with pytest.raises(study_server.ServerError) as excinfo:
        study_server.start("corpus-kb", "alpha", study, wait=1)
    assert "alpha.log" in str(excinfo.value)
