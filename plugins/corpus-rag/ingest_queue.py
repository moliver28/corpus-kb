"""The study-folder ingest queue (issue #352).

File: ``<study folder>/.claude/corpus-pending.jsonl`` with a ``.lock``
sidecar. Row keys: ``path, mtime, size, ts, kind`` (``transcript`` or
``file``). One line per path; a row dedups on (path, mtime) — the same
file with the same mtime never queues twice, and a changed file replaces
its older row.

Drain sends each row through one engine op. A transcript takes
``research_ingest_transcript`` then ``ingest_file`` (research tables plus
plain search); every other file takes ``queue_enqueue``. Rows for deleted
files drop with no engine call. Failed rows stay queued. Rows added while
the drain runs stay queued, because the final rewrite removes only the
exact (path, mtime, ts) rows this run handled.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

import registry_io
import transcripts
from file_locks import exclusive
from engine_paths import plugin_data_dir

QUEUE_DIRNAME = ".claude"
QUEUE_FILENAME = "corpus-pending.jsonl"
LOCK_SUFFIX = ".lock"
TMP_SUFFIX = ".tmp"
TRANSCRIPT = "transcript"
FILE = "file"
MTIME_EPSILON = 1e-6

Sender = Callable[[str, list[str]], dict]
Logger = Callable[[str, str, str], None]


def queue_path(folder_root: Path) -> Path:
    return Path(folder_root) / QUEUE_DIRNAME / QUEUE_FILENAME


def lock_path(folder_root: Path) -> Path:
    return queue_path(folder_root).with_name(QUEUE_FILENAME + LOCK_SUFFIX)


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _read_rows(path: Path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("path"), str):
            rows.append(row)
    return rows


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    body = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    tmp.write_text(body, encoding="utf-8")
    registry_io.replace_with_retry(tmp, path)


def queue_rows(folder_root: Path) -> list[dict]:
    """The queued rows, oldest first."""
    return _read_rows(queue_path(folder_root))


def enqueue(folder_root: Path, path: Path, kind: str | None = None) -> bool:
    """Queue one file; False when it is missing or already queued."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return False
    if kind is None:
        kind = TRANSCRIPT if transcripts.is_transcript(path) else FILE
    row = {
        "path": str(path),
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "ts": time.time(),
        "kind": kind,
    }
    queue = queue_path(folder_root)
    queue.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(lock_path(folder_root)):
        rows = _read_rows(queue)
        same_path = [r for r in rows if _key(r.get("path", "")) == _key(path)]
        if len(same_path) == 1:
            old = same_path[0]
            if abs(float(old.get("mtime", -1.0)) - stat.st_mtime) < MTIME_EPSILON:
                return False
        rows = [r for r in rows if _key(r.get("path", "")) != _key(path)]
        rows.append(row)
        _write_rows(queue, rows)
    return True


def remove_rows(folder_root: Path, gone: list[dict]) -> None:
    """Remove exact rows (path, mtime, ts); rows added meanwhile survive."""
    if not gone:
        return
    doomed = {(_key(row.get("path", "")), row.get("mtime"), row.get("ts")) for row in gone}
    queue = queue_path(folder_root)
    with exclusive(lock_path(folder_root)):
        rows = _read_rows(queue)
        keep = [
            row
            for row in rows
            if (_key(row.get("path", "")), row.get("mtime"), row.get("ts")) not in doomed
        ]
        if len(keep) != len(rows):
            _write_rows(queue, keep)


def drain(folder_root: Path, slug: str, sender: Sender, logger: Logger) -> dict:
    """Send every queued row. Returns counts: ingested, failed, dropped."""
    rows = queue_rows(folder_root)
    report = {"ingested": 0, "failed": 0, "dropped": 0}
    handled: list[dict] = []
    for row in rows:
        path = str(row.get("path", ""))
        route, result = _send_row(row, sender)
        logger(path, route, result)
        if result == "ok":
            report["ingested"] += 1
            handled.append(row)
        elif result == "dropped":
            report["dropped"] += 1
            handled.append(row)
        else:
            report["failed"] += 1
    remove_rows(folder_root, handled)
    return report


def _send_row(row: dict, sender: Sender) -> tuple[str, str]:
    """One row's engine calls. Returns (route, result).

    Results: ``ok``, ``dropped`` (file gone, no engine call), or
    ``failed: <detail>`` (the row stays queued).
    """
    path = str(row.get("path", ""))
    try:
        if not Path(path).is_file():
            return "none", "dropped"
        kind = str(row.get("kind") or FILE)
        if kind == TRANSCRIPT:
            sender("research_ingest_transcript", [f"file_path={path}"])
            sender("ingest_file", [f"file_path={path}"])
            return "research_ingest_transcript+ingest_file", "ok"
        sender("queue_enqueue", [f"path={path}"])
        return "queue_enqueue", "ok"
    except Exception as exc:  # noqa: BLE001 - one bad row must not stop the drain
        detail = f"{type(exc).__name__}: {exc}"[:200]
        return "none", f"failed: {detail}"


def ingest_log_path(slug: str) -> Path:
    return plugin_data_dir() / "ingest-log" / f"{slug}.jsonl"


def log_ingest(slug: str, path: str, route: str, result: str) -> None:
    """One row into ingest-log/<slug>.jsonl: ts, path, route, result."""
    row = {"ts": time.time(), "path": path, "route": route, "result": result}
    try:
        log = ingest_log_path(slug)
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass
