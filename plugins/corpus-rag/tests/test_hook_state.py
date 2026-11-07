"""Behavior tests for plugin hook state (issue #352).

State lives in ``${CLAUDE_PLUGIN_DATA}`` (or ``~/.corpus-kb/plugin/``):
``sessions/<id>.json`` behind a lock sidecar, ``hook-errors.jsonl``,
``telemetry.jsonl``, and the ``.pruned`` daily marker.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import hook_state  # noqa: E402


def setup_state(tmp_path: Path, monkeypatch) -> Path:
    data = tmp_path / "plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    return data


# ---------------------------------------------------------------------------
# Session ids and session files
# ---------------------------------------------------------------------------


def test_session_id_sanitized(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    assert hook_state.sanitize_session_id("abc-DEF_123") == "abc-DEF_123"
    assert hook_state.sanitize_session_id("../evil id!") == "___evil_id_"
    assert len(hook_state.sanitize_session_id("x" * 500)) == 120
    assert hook_state.sanitize_session_id("") == ""
    assert hook_state.sanitize_session_id("///") == ""


def test_session_roundtrip_with_lock_and_atomic_write(
    tmp_path: Path, monkeypatch
) -> None:
    data = setup_state(tmp_path, monkeypatch)
    hook_state.save_session("s1", {"turn": 1})
    loaded = hook_state.load_session("s1")
    assert loaded == {"turn": 1}
    assert (data / "sessions" / "s1.json").is_file()
    assert not list((data / "sessions").glob("*.tmp"))


def test_missing_session_loads_none(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    assert hook_state.load_session("nope") is None
    assert hook_state.load_session("") is None


def test_session_save_locks_the_sidecar(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    sidecar = data / "sessions" / "s1.json.lock"
    hook_state.save_session("s1", {"turn": 1})
    assert sidecar.exists()
    assert hook_state.load_session("s1") == {"turn": 1}


# ---------------------------------------------------------------------------
# Marks and the SessionStart 5 s dedup
# ---------------------------------------------------------------------------


def test_marks(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    assert not hook_state.mark_exists("daily-thing")
    hook_state.touch_mark("daily-thing")
    assert hook_state.mark_exists("daily-thing")


def test_dedup_same_event_within_window(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    assert not hook_state.dedup_hit("sessionstart", window=5.0)
    assert hook_state.dedup_hit("sessionstart", window=5.0)


def test_dedup_expires_after_window(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    hook_state.touch_mark("dedup-sessionstart", stamp=str(time.time() - 6.0))
    assert not hook_state.dedup_hit("sessionstart", window=5.0)


# ---------------------------------------------------------------------------
# jsonl appenders
# ---------------------------------------------------------------------------


def test_append_jsonl_rows(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    path = data / "telemetry.jsonl"
    hook_state.append_jsonl(path, {"a": 1})
    hook_state.append_jsonl(path, {"b": 2})
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"a": 1}, {"b": 2}]


def test_record_error_never_raises(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    hook_state.record_error("pre_tool_use", "boom")
    path = data / "hook-errors.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert row["where"] == "pre_tool_use"
    assert "boom" in row["detail"]
    # A state dir that cannot be written must not raise either.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a dir", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(blocker))
    hook_state.record_error("x", "y")


# ---------------------------------------------------------------------------
# Daily prune
# ---------------------------------------------------------------------------


def test_prune_runs_once_per_day(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    assert hook_state.prune_once(today="20261107") is True
    assert hook_state.prune_once(today="20261107") is False
    assert (data / ".pruned").read_text(encoding="utf-8").strip() == "20261107"
    assert hook_state.prune_once(today="20261108") is True


def test_prune_drops_old_sessions(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    sessions = data / "sessions"
    sessions.mkdir(parents=True)
    old = sessions / "old.json"
    old.write_text("{}", encoding="utf-8")
    stamp = time.time() - 31 * 24 * 3600
    os.utime(old, (stamp, stamp))
    fresh = sessions / "new.json"
    fresh.write_text("{}", encoding="utf-8")
    hook_state.prune_once(today="20261107")
    assert not old.exists()
    assert fresh.exists()


def test_prune_caps_jsonl_at_one_mb(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    data.mkdir(parents=True)
    path = data / "telemetry.jsonl"
    row = json.dumps({"pad": "x" * 200}) + "\n"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(row * 8000)  # ~1.6 MB
    hook_state.prune_once(today="20261107")
    size = path.stat().st_size
    assert size <= 1024 * 1024
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        json.loads(line)  # tail keeps whole lines


def test_prune_keeps_other_state(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    data.mkdir(parents=True)
    (data / "studies.json").write_text("{}", encoding="utf-8")
    hook_state.prune_once(today="20261107")
    assert (data / "studies.json").is_file()
