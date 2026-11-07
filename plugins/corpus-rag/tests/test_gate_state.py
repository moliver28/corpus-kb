"""Behavior tests for the setup gate state (issue #353).

One JSON file, atomic write behind a lock, one-time 0.7.0 marker
retirement that carries a prior "declined" choice over.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import folder_state  # noqa: E402
import gate_state  # noqa: E402


def setup_state(tmp_path: Path, monkeypatch) -> Path:
    data = tmp_path / "plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    return data


# ---------------------------------------------------------------------------
# Shape, atomic write, lock
# ---------------------------------------------------------------------------


def test_roundtrip_is_atomic_with_lock_sidecar(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    folder = tmp_path / "Study"
    folder.mkdir()
    gate_state.update_folder(folder, {"slug": "study"})
    path = data / "gate-state.json"
    assert path.is_file()
    assert not list(data.glob("*.tmp"))
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["folders"][gate_state.folder_key(folder)]["slug"] == "study"
    assert (data / "gate-state.json.lock").exists()


def test_folder_entry_default_is_empty(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = tmp_path / "Nope"
    assert gate_state.folder_entry(folder) == {}
    assert gate_state.is_declined(folder) is False


def test_snooze_stores_the_date_and_expires(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = tmp_path / "Study"
    folder.mkdir()
    until = gate_state.snooze(folder, days=7)
    assert gate_state.snoozed_until_today(folder) == until
    state = gate_state.load_state()
    state["folders"][gate_state.folder_key(folder)]["snoozed_until"] = "2020-01-01"
    gate_state.save_state(state)
    assert gate_state.snoozed_until_today(folder) is None


def test_decline_and_local_only(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = tmp_path / "Study"
    folder.mkdir()
    gate_state.decline(folder)
    assert gate_state.is_declined(folder) is True
    gate_state.set_local_only(folder, True)
    assert gate_state.folder_entry(folder)["local_only"] is True


def test_folders_enabled_by_setup_filters_missing(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = tmp_path / "Study"
    folder.mkdir()
    gone = tmp_path / "Gone"
    gate_state.update_folder(folder, {"enabled_by_setup": True})
    gate_state.update_folder(gone, {"enabled_by_setup": True})
    assert gate_state.folders_enabled_by_setup() == [folder.resolve()]


def test_concurrent_writes_keep_valid_json(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = tmp_path / "Study"
    folder.mkdir()
    errors: list[Exception] = []

    def writer(index: int) -> None:
        try:
            gate_state.update_folder(folder, {"attempt": index})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    final = json.loads(
        (tmp_path / "plugin-data" / "gate-state.json").read_text(encoding="utf-8")
    )
    assert isinstance(final, dict)


# ---------------------------------------------------------------------------
# One-time 0.7.0 marker retirement
# ---------------------------------------------------------------------------


def test_retire_carries_declined_and_removes_marks(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    marks = data / "marks"
    marks.mkdir(parents=True)
    folder = tmp_path / "Study"
    folder.mkdir()
    legacy_declined = marks / folder_state.declined_mark_name(folder)
    legacy_declined.write_text("", encoding="utf-8")
    (marks / "study-registered").write_text("", encoding="utf-8")
    (marks / "unrelated-mark").write_text("", encoding="utf-8")

    state = gate_state.load_state()
    assert state["retired_070"] is True
    assert folder_state.declined_mark_name(folder) in state["legacy_declined"]
    assert not legacy_declined.exists()
    assert not (marks / "study-registered").exists()
    assert (marks / "unrelated-mark").exists()
    # The carried-over entry declines the folder.
    assert gate_state.is_declined(folder) is True
    assert folder_state.is_declined(folder) is True


def test_retire_runs_once(tmp_path: Path, monkeypatch) -> None:
    data = setup_state(tmp_path, monkeypatch)
    marks = data / "marks"
    marks.mkdir(parents=True)
    (marks / "study-registered").write_text("", encoding="utf-8")
    gate_state.load_state()
    assert gate_state.load_state()["retired_070"] is True
    # A new 0.7.0-style mark written after retirement stays untouched.
    (marks / "study-registered").write_text("", encoding="utf-8")
    state = gate_state.load_state()
    assert state["retired_070"] is True
    assert (marks / "study-registered").exists()


def test_entry_declined_survives_without_marks(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = tmp_path / "Study"
    folder.mkdir()
    gate_state.decline(folder)
    assert not (gate_state.marks_dir() / folder_state.declined_mark_name(folder)).exists()
    assert folder_state.is_declined(folder) is True
