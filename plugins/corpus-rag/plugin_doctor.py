"""``corpus-rag plugin-doctor``: the plugin's own health report.

Versions (plugin, Python, Claude Code, engine, minimum engine), hook
file state, trigger file state, folder state, and the last 10 hook
errors that mention this folder. Exit 0 only when the hook files and
the trigger file are good. ``--bundle`` writes one redacted JSON file
for a bug report: machine paths, DSN passwords, and email addresses
come out.

Stdlib only.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import engine_install
import engine_paths
import folder_state
import gate_state
import preflight
import triggers

PLUGIN_ROOT = Path(__file__).resolve().parent
HOOK_BODIES = (
    "hooks/hooks.json",
    "hooks/dispatch.sh",
    "hooks/hook_env.sh",
    "hooks/session_start.py",
    "hooks/prompt_submit.py",
    "hooks/subagent_start.py",
    "hooks/pre_tool_use.py",
    "hooks/post_tool_use.py",
    "hooks/file_changed.py",
    "hooks/stop.py",
    "hooks/triggers.json",
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DSN_PASSWORD_RE = re.compile(r"(://[^:@/]+:)[^@]+(@)")


def claude_version() -> str:
    """The Claude Code or ZCode version, or ``unknown``."""
    found = shutil.which("claude")
    if not found:
        return "unknown"
    try:
        result = subprocess.run(
            [found, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    match = re.search(r"\d+\.\d+\.\d+", result.stdout or "")
    return match.group(0) if match else "unknown"


def hook_file_state() -> dict:
    """One row per hook file: present or missing."""
    rows = {}
    missing = []
    for rel in HOOK_BODIES:
        ok = (PLUGIN_ROOT / rel).is_file()
        rows[rel] = ok
        if not ok:
            missing.append(rel)
    return {"files": rows, "missing": missing, "ok": not missing}


def trigger_state() -> dict:
    """The trigger file: readable, with the three lists."""
    data = triggers.load_triggers(PLUGIN_ROOT)
    ok = bool(data["nouns"]) and bool(data["frames"])
    return {
        "ok": ok,
        "nouns": len(data["nouns"]),
        "frames": len(data["frames"]),
        "exclusions": len(data["exclusions"]),
    }


def engine_report() -> dict:
    """Engine root, version, and the minimum the plugin needs."""
    root = engine_paths.engine_root()
    cli = engine_paths.engine_cli(root) if root else None
    version = preflight.engine_version(cli) if cli else ""
    return {
        "root": str(root) if root else None,
        "version": version or "unknown",
        "minimum": preflight.MINIMUM_ENGINE_VERSION,
        "ok": bool(version)
        and preflight._version_tuple(version)
        >= preflight._version_tuple(preflight.MINIMUM_ENGINE_VERSION),
    }


def folder_report(cwd: Path) -> dict:
    """The folder verdict plus its gate-state entry."""
    fs = folder_state.folder_state(cwd)
    entry = gate_state.folder_entry(cwd)
    return {"state": fs.state, "path": str(cwd), "gate": entry}


def hook_errors_for(folder: Path, limit: int = 10) -> list[dict]:
    """The last ``limit`` hook-error rows that mention this folder."""
    log = engine_paths.plugin_data_dir() / "hook-errors.jsonl"
    rows: list[dict] = []
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    mark = str(folder)
    for line in lines[-500:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        detail = str(row.get("detail", ""))
        if mark in detail or mark in str(row.get("where", "")):
            rows.append(
                {"ts": row.get("ts"), "where": row.get("where"), "detail": detail[:200]}
            )
    return rows[-limit:]


def report(cwd: Path) -> dict:
    """The whole doctor report (unredacted; the printed lines come from it)."""
    return {
        "versions": {
            "plugin": engine_install.plugin_version(),
            "python": sys.version.split()[0],
            "claude": claude_version(),
            "engine": engine_report(),
        },
        "hooks": hook_file_state(),
        "triggers": trigger_state(),
        "folder": folder_report(cwd),
        "hook_errors": hook_errors_for(cwd),
    }


# ---------------------------------------------------------------------------
# Redaction and the bundle
# ---------------------------------------------------------------------------


def redact(text: str, folder: Path) -> str:
    """Machine paths, DSN passwords, and emails out of one string.

    Most specific first: the folder, then the plugin data folder, then
    the home folder. Home first erases the longer prefixes, so folder
    and data-dir names leak.
    """
    text = text.replace(str(folder), "<folder>")
    text = text.replace(str(engine_paths.plugin_data_dir()), "<data>")
    text = text.replace(str(Path.home()), "~")
    text = DSN_PASSWORD_RE.sub(r"\1***\2", text)
    return EMAIL_RE.sub("<email>", text)


def redacted_bundle(cwd: Path) -> dict:
    """The report with every machine path and credential redacted."""
    data = report(cwd)
    versions = data["versions"]
    engine = versions["engine"]
    engine["root"] = (
        redact(engine["root"], cwd) if isinstance(engine.get("root"), str) else None
    )
    data["folder"]["path"] = "<folder>"
    gate = data["folder"].get("gate") or {}
    data["folder"]["gate"] = {
        key: gate.get(key)
        for key in ("declined", "snoozed_until", "slug", "enabled_by_setup", "local_only")
    }
    data["hook_errors"] = [
        {
            "ts": row.get("ts"),
            "where": row.get("where"),
            "detail": redact(str(row.get("detail", "")), cwd),
        }
        for row in data["hook_errors"]
    ]
    return data


def bundle_path() -> Path:
    return engine_paths.plugin_data_dir() / "plugin-doctor-bundle.json"


def write_bundle(cwd: Path) -> Path:
    """One redacted JSON file for a bug report."""
    path = bundle_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redacted_bundle(cwd), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
