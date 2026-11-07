"""The setup gate state, one JSON file for offer decisions (issue #353).

File: ``${CLAUDE_PLUGIN_DATA}/gate-state.json`` (else
``~/.corpus-kb/plugin/gate-state.json``). Shape::

    {"retired_070": true,
     "legacy_declined": ["declined-folder-123"],
     "folders": {"<abs folder path>": {
         "declined": false, "snoozed_until": "2026-11-14",
         "slug": "interviews", "enabled_by_setup": true,
         "local_only": false}}}

Writes hold a lock sidecar and land with an atomic rename. On first use
the one-time 0.7.0 marker retirement runs: ``study-registered`` and the
``declined-*`` mark files are read once (a prior "declined" choice moves
into ``legacy_declined``) and then removed.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import date, timedelta
from pathlib import Path

import registry_io
from engine_paths import plugin_data_dir
from file_locks import exclusive

FILENAME = "gate-state.json"
LOCK_SUFFIX = ".lock"
TMP_SUFFIX = ".tmp"
LEGACY_REGISTERED_MARK = "study-registered"
LEGACY_DECLINED_FOLDER_PREFIX = "declined-folder-"
RETIRE_FLAG = "retired_070"
SNOOZE_DAYS_DEFAULT = 7


def declined_mark_name(folder: Path) -> str:
    """The legacy folder-declined mark name (same shape folder_state uses)."""
    return f"{LEGACY_DECLINED_FOLDER_PREFIX}{_crc(folder)}"


def gate_state_path() -> Path:
    return plugin_data_dir() / FILENAME


def marks_dir() -> Path:
    return plugin_data_dir() / "marks"


def folder_key(folder: Path) -> str:
    """The gate-state key for one folder: the resolved absolute path."""
    try:
        return str(Path(folder).resolve())
    except OSError:
        return str(folder)


def empty_state() -> dict:
    return {"retired_070": False, "legacy_declined": [], "folders": {}}


def _read() -> dict:
    path = gate_state_path()
    if not path.is_file():
        return empty_state()
    try:
        data = json.loads(registry_io.read_with_retry(path))
    except (OSError, ValueError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    data.setdefault("legacy_declined", [])
    data.setdefault("folders", {})
    return data


def _write(data: dict) -> None:
    path = gate_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = Path(str(path) + LOCK_SUFFIX)
    with exclusive(lock, wait=5.0):
        tmp = Path(str(path) + TMP_SUFFIX)
        tmp.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        registry_io.replace_with_retry(tmp, path)


def _retire_070(data: dict) -> dict:
    """One-time 0.7.0 marker retirement: read once, carry over, remove.

    ``study-registered`` goes away, and the ``declined-folder-*`` marks
    move into ``legacy_declined`` so a prior "declined" choice carries
    over. The prompt-lane marks (``declined-ask`` and friends) are not
    setup state; they stay.
    """
    marks = marks_dir()
    try:
        names = [item.name for item in marks.iterdir() if item.is_file()]
    except OSError:
        names = []
    for name in names:
        if name == LEGACY_REGISTERED_MARK:
            _unlink(marks / name)
        elif name.startswith(LEGACY_DECLINED_FOLDER_PREFIX):
            if name not in data["legacy_declined"]:
                data["legacy_declined"].append(name)
            _unlink(marks / name)
    data[RETIRE_FLAG] = True
    return data


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def peek_state() -> dict:
    """The gate state without running the retirement (no writes)."""
    return _read()


def load_state() -> dict:
    """The gate state, with the one-time retirement run and saved once."""
    data = _read()
    if not data.get(RETIRE_FLAG):
        data = _retire_070(data)
        _write(data)
    return data


def save_state(data: dict) -> None:
    _write(data)


def folder_entry(folder: Path, data: dict | None = None) -> dict:
    """One folder's entry, empty when absent. Never writes."""
    if data is None:
        data = peek_state()
    row = data.get("folders", {}).get(folder_key(folder))
    return row if isinstance(row, dict) else {}


def update_folder(folder: Path, fields: dict, data: dict | None = None) -> dict:
    """Merge ``fields`` into one folder's entry and save. Returns the state."""
    if data is None:
        data = load_state()
    key = folder_key(folder)
    entry = data.setdefault("folders", {}).setdefault(key, {})
    entry.update(fields)
    save_state(data)
    return data


def is_declined(folder: Path, data: dict | None = None) -> bool:
    """True when the folder is declined: the entry, a legacy mark, or both."""
    name = declined_mark_name(folder)
    if (marks_dir() / name).is_file():
        return True
    if data is None:
        data = peek_state()
    if name in data.get("legacy_declined", []):
        return True
    return bool(folder_entry(folder, data).get("declined"))


def snoozed_until_today(folder: Path, data: dict | None = None) -> str | None:
    """The snooze date when it covers today, else None."""
    raw = folder_entry(folder, data).get("snoozed_until")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        until = date.fromisoformat(raw)
    except ValueError:
        return None
    if until < date.today():
        return None
    return raw


def snooze(folder: Path, days: int = SNOOZE_DAYS_DEFAULT, data: dict | None = None) -> str:
    """Hide the offer for N days. Returns the ISO date it hides until."""
    until = (date.today() + timedelta(days=max(1, int(days)))).isoformat()
    update_folder(folder, {"snoozed_until": until}, data)
    return until


def decline(folder: Path, data: dict | None = None) -> None:
    """Decline the offer for good in this folder."""
    update_folder(folder, {"declined": True}, data)


def set_local_only(folder: Path, local_only: bool, data: dict | None = None) -> None:
    """Store the cloud-model consent outcome for the UXR layer."""
    update_folder(folder, {"local_only": bool(local_only)}, data)


def folders_enabled_by_setup(data: dict | None = None) -> list[Path]:
    """Every folder whose setup set ``enabled_by_setup`` and still exists."""
    if data is None:
        data = load_state()
    out = []
    for key, entry in sorted(data.get("folders", {}).items()):
        if isinstance(entry, dict) and entry.get("enabled_by_setup"):
            path = Path(key)
            if path.is_dir():
                out.append(path)
    return out


def _crc(folder: Path) -> int:
    import zlib

    return zlib.crc32(folder_key(folder).encode("utf-8"))


def now() -> float:
    return time.time()
