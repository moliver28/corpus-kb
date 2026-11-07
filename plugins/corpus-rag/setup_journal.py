"""The setup journal: ``<folder>/.claude/corpus.setup.json`` (issue #353).

One row per step::

    {"name": "allow_rules", "state": "done", "undo": {...}, "ts": ...}

``state`` is ``done`` or ``failed``. Each done step carries the ``undo``
record its ``--off`` needs (never for ``database`` and ``config``). A
retry skips done steps and resumes at the first not-done one; the failed
step name stays in ``failed_step`` until it passes.

The journal holds the answers too, so a plain
``corpus-rag setup --apply`` (no ``--answers``) resumes a half-run setup.
Writes are atomic: temp file, rename. Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

import registry_io

JOURNAL_DIR = ".claude"
JOURNAL_NAME = "corpus.setup.json"
STEP_ORDER = ("database", "allow_rules", "engine_override", "config")


def journal_path(folder: Path) -> Path:
    return Path(folder) / JOURNAL_DIR / JOURNAL_NAME


def load(folder: Path) -> dict | None:
    """The journal, or None when absent or unreadable."""
    path = journal_path(folder)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save(folder: Path, data: dict) -> None:
    """One atomic journal write (temp file, rename)."""
    path = journal_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    registry_io.replace_with_retry(tmp, path)


def new_journal(folder: Path, answers: dict, slug: str) -> dict:
    return {"folder": str(folder), "slug": slug, "answers": answers, "steps": []}


def step_rows(journal: dict) -> dict[str, dict]:
    """The recorded rows keyed by step name, last row wins."""
    rows: dict[str, dict] = {}
    for row in journal.get("steps", []):
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            rows[row["name"]] = row
    return rows


def done_steps(journal: dict) -> set[str]:
    """The names of steps recorded ``done``."""
    return {
        name for name, row in step_rows(journal).items() if row.get("state") == "done"
    }


def record_step(journal: dict, name: str, state: str, undo: dict | None = None) -> None:
    row: dict = {"name": name, "state": state, "ts": _now()}
    if undo is not None:
        row["undo"] = undo
    journal.setdefault("steps", []).append(row)
    journal["failed_step"] = name if state == "failed" else None


def pending_step(journal: dict) -> str | None:
    """The first step in order that is not done yet."""
    done = done_steps(journal)
    for name in STEP_ORDER:
        if name not in done:
            return name
    return None


def undo_records(journal: dict) -> dict[str, dict]:
    """The undo records of done steps, keyed by step name."""
    return {
        name: row.get("undo") or {}
        for name, row in step_rows(journal).items()
        if row.get("state") == "done"
    }


def _now() -> float:
    import time

    return time.time()
