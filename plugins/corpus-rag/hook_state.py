"""Plugin state for hooks: sessions, marks, logs, daily prune (issue #352).

Everything lives under ``${CLAUDE_PLUGIN_DATA}`` (or
``~/.corpus-kb/plugin/``):

- ``sessions/<id>.json`` — one file per session, behind a ``.lock``
  sidecar, written atomically (temp file, then rename). Session ids are
  limited to ``[A-Za-z0-9_-]`` and 120 characters.
- ``hook-errors.jsonl`` — one line per swallowed hook failure.
- ``telemetry.jsonl`` — one line per turn that read a transcript by hand.
- ``marks/`` — dated and dedup marks.
- ``.pruned`` — the last prune date. Once a day the SessionStart hook
  removes sessions older than 30 days and caps the two jsonl logs at 1 MB.

Stdlib only. Every function here is safe to call from a failing hook:
appenders and prune swallow their own errors and record nothing.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path

import registry_io
from engine_paths import plugin_data_dir
from file_locks import exclusive

SESSION_ID_PATTERN = re.compile(r"[^A-Za-z0-9_-]")
SESSION_ID_MAX = 120
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600
LOG_CAP_BYTES = 1024 * 1024
PRUNE_MARKER = ".pruned"
DEDUP_PREFIX = "dedup-"
DEDUP_DEFAULT_WINDOW = 5.0


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def sessions_dir() -> Path:
    return plugin_data_dir() / "sessions"


def marks_dir() -> Path:
    return plugin_data_dir() / "marks"


def session_path(session_id: str) -> Path:
    return sessions_dir() / f"{session_id}.json"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def sanitize_session_id(raw: str) -> str:
    """Keep ``[A-Za-z0-9_-]`` only; empty ids never touch the disk."""
    clean = SESSION_ID_PATTERN.sub("_", raw or "")[:SESSION_ID_MAX]
    return clean if clean.strip("_") else ""


def exclusive_session_lock(session_id: str):
    """The lock sidecar for one session file."""
    return exclusive(sessions_dir() / f"{session_id}.json.lock")


def load_session(session_id: str) -> dict | None:
    """One session record, or None when absent or unreadable."""
    if not session_id:
        return None
    try:
        raw = session_path(session_id).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_session(session_id: str, data: dict) -> None:
    """One session record: lock, temp file, atomic rename."""
    if not session_id:
        return
    path = session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_session_lock(session_id):
        tmp = Path(f"{path}.tmp")
        tmp.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
        registry_io.replace_with_retry(tmp, path)


def fresh_session(slug: str, folder: Path) -> dict:
    """The shape of a new session record."""
    stamp = now()
    return {
        "created": stamp,
        "last": stamp,
        "slug": slug,
        "folder": str(folder),
        "turn": 0,
        "turn_reads": [],
        "first_read_at": None,
        "corpus_called_at": None,
        "nudges": 0,
        "subagent": False,
    }


# ---------------------------------------------------------------------------
# Marks and dedup
# ---------------------------------------------------------------------------


def mark_path(name: str) -> Path:
    return marks_dir() / name


def mark_exists(name: str) -> bool:
    return mark_path(name).is_file()


def touch_mark(name: str, stamp: str | None = None) -> None:
    """Create one mark file, best effort."""
    path = mark_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stamp if stamp is not None else "", encoding="utf-8")
    except OSError:
        pass


def dedup_hit(event: str, window: float = DEDUP_DEFAULT_WINDOW) -> bool:
    """True when the same event fired inside the window (default 5 s)."""
    name = f"{DEDUP_PREFIX}{event}"
    path = mark_path(name)
    try:
        seen = float(path.read_text(encoding="utf-8").strip() or 0.0)
    except (OSError, ValueError):
        seen = 0.0
    if seen and (time.time() - seen) < window:
        return True
    touch_mark(name, stamp=str(time.time()))
    return False


# ---------------------------------------------------------------------------
# jsonl appenders
# ---------------------------------------------------------------------------


def append_jsonl(path: Path, row: dict) -> None:
    """One JSON line at the end of a log file, best effort."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


def record_error(where: str, detail: str) -> None:
    """One swallowed hook failure into hook-errors.jsonl. Never raises."""
    row = {"ts": now(), "where": where, "detail": detail[:400]}
    with contextlib.suppress(Exception):
        append_jsonl(plugin_data_dir() / "hook-errors.jsonl", row)


def now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Daily prune
# ---------------------------------------------------------------------------


def prune_once(
    today: str | None = None,
    max_age: float = SESSION_MAX_AGE_SECONDS,
    cap: int = LOG_CAP_BYTES,
) -> bool:
    """Prune state once per calendar day. True when work ran today."""
    day = today if today is not None else time.strftime("%Y%m%d")
    marker = plugin_data_dir() / PRUNE_MARKER
    try:
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == day:
            return False
    except OSError:
        pass
    _prune_sessions(max_age)
    for name in ("hook-errors.jsonl", "telemetry.jsonl"):
        _cap_jsonl(plugin_data_dir() / name, cap)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(day, encoding="utf-8")
    except OSError:
        pass
    return True


def _prune_sessions(max_age: float) -> None:
    cutoff = time.time() - max_age
    try:
        rows = list(sessions_dir().iterdir())
    except OSError:
        return
    for path in rows:
        if not path.name.endswith(".json"):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _cap_jsonl(path: Path, cap: int) -> None:
    """Keep the tail of a log file under the byte cap, whole lines only."""
    try:
        if path.stat().st_size <= cap:
            return
        raw = path.read_bytes()[-cap:]
        newline = raw.find(b"\n")
        if newline >= 0:
            raw = raw[newline + 1 :]
        tmp = Path(f"{path}.tmp")
        tmp.write_bytes(raw)
        registry_io.replace_with_retry(tmp, path)
    except OSError:
        pass
