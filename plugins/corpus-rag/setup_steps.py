"""The journaled setup steps and their undos (issue #353).

Four steps, in order, each recorded in ``.claude/corpus.setup.json``:

``database``        ``corpus-rag provision <slug>``. Undo: never — the
                    data stays.
``allow_rules``     Add the read-only Bash allow rules (plus
                    ``uxr insights`` in a UXR study) to
                    ``.claude/settings.json``. Undo: remove them.
``engine_override`` Set ``queue.autorun: true`` in the overlay when
                    chosen. Undo: restore the prior overlay text.
``config``          Write ``.claude/corpus.json``; seed-ingest the
                    smallest watched file; queue the rest. Undo: never.

Slug rule: ``corpus.json`` wins; else the folder name; if another folder
owns that slug (gate state or the study registry), append six hex
characters of the SHA-1 of the folder path.

Engine runs go through ``run_cli`` (the plugin's own ``bin/corpus-rag``);
tests replace it. No test may run a real provision. Stdlib only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import ingest_queue
import registry_io
import transcripts

PLUGIN_BIN = Path(__file__).resolve().parent / "bin" / "corpus-rag"
ALLOW_RULES = (
    "Bash(corpus-rag search:*)",
    "Bash(corpus-rag ask:*)",
    "Bash(corpus-rag status:*)",
)
UXR_RULE = "Bash(uxr insights:*)"
OVERLAY_MAX_UNDO_BYTES = 64 * 1024


class StepError(Exception):
    """A step failed; the journal keeps the failure for the retry."""


def run_cli(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    """One plugin CLI call with this interpreter (tests monkeypatch this)."""
    return subprocess.run(
        [sys.executable, str(PLUGIN_BIN), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_database(folder: Path, plan: dict) -> dict | None:
    """Provision the study. Undo: never — the data stays."""
    args = ["provision", plan["slug"]]
    if plan.get("backend"):
        args += ["--backend", plan["backend"]]
    result = run_cli(args)
    if result.returncode != 0:
        raise StepError(
            "provision failed: " + ((result.stdout or result.stderr or "").strip()[-400:])
        )
    return None


def _settings_path(folder: Path) -> Path:
    return Path(folder) / ".claude" / "settings.json"


def step_allow_rules(folder: Path, plan: dict) -> dict:
    """Add the allow rules. Undo removes exactly the rows this step added."""
    rules = list(ALLOW_RULES)
    if plan.get("uxr"):
        rules.append(UXR_RULE)
    path = _settings_path(folder)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    allow = permissions.get("allow")
    allow = [str(row) for row in allow] if isinstance(allow, list) else []
    added = [rule for rule in rules if rule not in allow]
    if added:
        data["permissions"] = {"allow": allow + added, **{
            key: value for key, value in permissions.items() if key != "allow"
        }}
        _write_json(path, data)
    return {"added": added, "path": str(path)}


def undo_allow_rules(folder: Path, undo: dict | None = None) -> list[str]:
    """Remove the rules the step added (or the standard set without a journal)."""
    record = undo or {}
    added = [str(row) for row in record.get("added", [])] or list(ALLOW_RULES)
    path = Path(str(record.get("path") or _settings_path(folder)))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        return []
    before = [str(row) for row in permissions.get("allow", [])]
    allow = [row for row in before if row not in added]
    if allow == before:
        return []
    permissions["allow"] = allow
    _write_json(path, data)
    return [row for row in before if row not in allow]


def overlay_target() -> Path:
    """The overlay file to edit: the legacy one wins, else ours (created)."""
    import study_config

    legacy = study_config.state_dir() / study_config.LEGACY_OVERLAY_FILENAME
    if legacy.is_file():
        return legacy
    return study_config.plugin_data_dir() / study_config.OVERLAY_FILENAME


def _overlay_autorun_line(text: str, value: str) -> str:
    """The overlay text with ``queue.autorun`` set, line surgery only."""
    lines = text.splitlines()
    in_queue = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        top = not line[0].isspace()
        if top:
            in_queue = stripped.startswith("queue:")
            continue
        if in_queue and stripped.startswith("autorun:"):
            lines[index] = line[: len(line) - len(stripped)] + f"autorun: {value}"
            return "\n".join(lines) + "\n"
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "queue:\n  autorun: true\n"


def step_engine_override(folder: Path, plan: dict) -> dict | None:
    """Set ``queue.autorun: true`` in the overlay. Undo restores the text."""
    if not plan.get("autorun"):
        return {"skipped": True}
    path = overlay_target()
    try:
        before = path.read_text(encoding="utf-8")
    except OSError:
        before = ""
    if len(before.encode("utf-8")) > OVERLAY_MAX_UNDO_BYTES:
        raise StepError(f"The overlay at {path} is too large to edit safely.")
    after = _overlay_autorun_line(before, "true")
    if after != before:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(after, encoding="utf-8")
    return {"path": str(path), "before": before, "changed": after != before}


def undo_engine_override(folder: Path, undo: dict) -> None:
    if not undo or undo.get("skipped"):
        return
    path = Path(str(undo.get("path") or overlay_target()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(undo.get("before", "")), encoding="utf-8")


def step_config(folder: Path, plan: dict) -> dict | None:
    """Write corpus.json, seed-ingest the smallest file, queue the rest."""
    marker = {
        "slug": plan["slug"],
        "watch": plan["watch"],
        "auto_ingest": plan["auto_ingest"],
    }
    path = Path(folder) / ".claude" / "corpus.json"
    _write_json(path, marker)
    files = _watch_files(folder, plan["watch"])
    smallest = min(files, key=lambda item: _size(item)) if files else None
    seed = None
    if smallest is not None:
        result = run_cli(["ingest", str(smallest), "-p", plan["slug"]])
        if result.returncode != 0:
            raise StepError(
                "seed ingest failed: "
                + ((result.stdout or result.stderr or "").strip()[-300:])
            )
        seed = str(smallest)
    queued = 0
    for item in files:
        if smallest is not None and item == smallest:
            continue
        if ingest_queue.enqueue(folder, item):
            queued += 1
    return {"seed": seed, "queued": queued}


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 1 << 60


def _watch_files(folder: Path, watch: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in watch:
        base = (Path(folder) / item).resolve()
        if base.is_file():
            files.append(base)
        elif base.is_dir():
            files.extend(
                path
                for path in transcripts.scan_files(base, list(transcripts.DEFAULT_EXCLUDES))
                if path.is_file()
            )
    return sorted(set(files))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    registry_io.replace_with_retry(tmp, path)


RUNNERS = {
    "database": step_database,
    "allow_rules": step_allow_rules,
    "engine_override": step_engine_override,
    "config": step_config,
}

UNDOERS = {
    "allow_rules": undo_allow_rules,
    "engine_override": undo_engine_override,
}
