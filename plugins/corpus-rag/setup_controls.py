"""The setup-wave control commands (issue #353).

``snooze``, ``decline``, ``pause``, ``resume``, ``signals``, and
``plugin-doctor``. They run from the folder they act on. ``pause`` and
``resume`` touch only ``enabled`` in ``.claude/corpus.json``. Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

import gate_state
import plugin_doctor
import setup_offer
import study_registry
import study_server


def _cwd() -> Path:
    return Path.cwd()


def _stop_study(slug: str) -> list[str]:
    """Stop one study's server when it is registered. Lines to print."""
    try:
        registry = study_registry.load_registry()
        if slug not in registry.get("studies", {}):
            return []
        if study_server.stop_study(slug, registry["studies"][slug]):
            study_registry.save_registry(registry)
            return [f"- study '{slug}' server stopped"]
    except study_registry.RegistryError:
        return []
    return []




def cmd_snooze(args) -> int:
    """Hide the offer in this folder for N days (default 7)."""
    folder = _cwd()
    until = gate_state.snooze(folder, getattr(args, "days", 7) or 7)
    print(f"Setup offer snoozed in this folder until {until}.")
    return 0


def cmd_decline(_args) -> int:
    """Never offer setup in this folder again."""
    folder = _cwd()
    gate_state.decline(folder)
    setup_offer.remove_sentinel(folder)
    print("Setup declined for this folder. Run `corpus-rag setup --questions` to change this.")
    return 0


def _marker(folder: Path) -> dict:
    path = folder / ".claude" / "corpus.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_marker(folder: Path, data: dict) -> None:
    path = folder / ".claude" / "corpus.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd_pause(_args) -> int:
    """Stop the study server and set enabled: false. Nothing else changes."""
    folder = _cwd()
    data = _marker(folder)
    if not data:
        print("No corpus study here. Run pause inside a folder with .claude/corpus.json.")
        return 1
    slug = data.get("slug") or data.get("study")
    if slug:
        _stop_study(str(slug))
    data["enabled"] = False
    _write_marker(folder, data)
    print(f"Study '{slug}' paused: server stopped, hooks off in this folder.")
    return 0


def cmd_resume(_args) -> int:
    """Remove the enabled: false flag. Nothing else changes."""
    folder = _cwd()
    data = _marker(folder)
    if not data:
        print("No corpus study here. Run resume inside a folder with .claude/corpus.json.")
        return 1
    if "enabled" in data:
        del data["enabled"]
        _write_marker(folder, data)
    slug = data.get("slug") or data.get("study")
    print(f"Study '{slug}' resumed: hooks run in this folder again.")
    return 0


def cmd_signals(_args) -> int:
    """Print the signal-scan counts for this folder."""
    folder = _cwd()
    counts = setup_offer.signal_scan(folder)
    counts["folder"] = str(folder)
    print(json.dumps(counts, indent=2))
    if setup_offer.has_signal(counts):
        setup_offer.touch_sentinel(folder)
    return 0


def cmd_plugin_doctor(args) -> int:
    """Plugin health: versions, hook files, triggers, folder, hook errors."""
    folder = _cwd()
    data = plugin_doctor.report(folder)
    versions = data["versions"]
    engine = versions["engine"]
    print(f"plugin: corpus-rag {versions['plugin']}")
    print(f"python: {versions['python']}")
    print(f"claude: {versions['claude']}")
    engine_line = f"engine: {engine['version']} (minimum {engine['minimum']})"
    print(engine_line if engine["ok"] else engine_line + " — update the engine")
    hooks = data["hooks"]
    if hooks["ok"]:
        print("hooks: ok")
    else:
        print("hooks: not ok: missing " + ", ".join(hooks["missing"]))
    triggers_row = data["triggers"]
    print(
        f"triggers: {'ok' if triggers_row['ok'] else 'not ok'} "
        f"({triggers_row['nouns']} nouns, {triggers_row['frames']} frames, "
        f"{triggers_row['exclusions']} exclusions)"
    )
    print(f"folder: {data['folder']['state']}")
    errors = data["hook_errors"]
    print(f"hook errors for this folder: {len(errors)}")
    for row in errors:
        print(f"  {row.get('ts')} {row.get('where')}: {row.get('detail')}")
    if getattr(args, "bundle", False):
        path = plugin_doctor.write_bundle(folder)
        print(f"Bundle written: {path} (redacted)")
    return 0 if data["hooks"]["ok"] and data["triggers"]["ok"] else 1
