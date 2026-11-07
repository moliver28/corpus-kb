"""The setup-wave command bodies (issue #353).

``setup`` (``--questions``, ``--apply --answers``, ``--off [--all]``),
``snooze``, ``decline``, ``pause``, ``resume``, ``signals``, and
``plugin-doctor``. Every body returns the process exit code; ``main``
prints typed errors. ``setup --questions`` and ``signals`` change
nothing on disk. Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

import gate_state
import pii_scan
import preflight
import setup_apply
import setup_journal
import setup_offer
import setup_questions
import setup_steps
import study_registry
import study_server
from setup_apply import StepError


def _cwd() -> Path:
    return Path.cwd()


def cmd_setup(args) -> int:
    """Route the setup flags: --questions, --apply, --off."""
    if getattr(args, "questions", False):
        return cmd_setup_questions(args)
    if getattr(args, "apply", False):
        return cmd_setup_apply(args)
    if getattr(args, "off", False):
        return cmd_setup_off(args)
    print("Choose one: setup --questions, setup --apply --answers <json|@file>, or setup --off.")
    return 2


# ---------------------------------------------------------------------------
# setup --questions
# ---------------------------------------------------------------------------


def cmd_setup_questions(args) -> int:
    """Preflight, PII scan, and the question payload. Changes nothing."""
    folder = _cwd()
    backend = getattr(args, "backend", None)
    report = preflight.run(backend)
    failed = preflight.hard_failures(report)
    if failed:
        print(json.dumps({"ok": False, "failed": failed}, indent=2))
        for row in failed:
            print(f"Fix: {row['fix']}")
        return 1
    if setup_offer.research_blocker(folder) == "corpus":
        print("This folder already has a corpus study in .claude/corpus.json.")
        return 0
    counts = setup_offer.signal_scan(folder)
    subfolders = _subfolders(folder)
    pii_rows = pii_scan.scan_folder(folder)
    payload = setup_questions.payload(folder.name, subfolders, pii_rows, report["soft"], backend)
    payload["signals"] = counts
    print(json.dumps(payload, indent=2))
    return 0


def _subfolders(folder: Path) -> list[str]:
    try:
        return sorted(
            item.name
            for item in folder.iterdir()
            if item.is_dir() and not item.name.startswith(".")
            and item.name not in setup_offer.SIGNAL_DIR_EXCLUDES
        )[:4]
    except OSError:
        return []


# ---------------------------------------------------------------------------
# setup --apply / --off
# ---------------------------------------------------------------------------


def _load_answers(args, folder: Path) -> dict:
    raw = getattr(args, "answers", None)
    if raw:
        text = Path(raw[1:]).read_text(encoding="utf-8") if raw.startswith("@") else raw
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("The answers must be one JSON object keyed by question header.")
        return data
    journal = setup_journal.load(folder)
    if journal and isinstance(journal.get("answers"), dict):
        return journal["answers"]
    raise ValueError(
        "No answers. Pass --answers '<json>' or --answers @file, or re-run setup --questions."
    )


def cmd_setup_apply(args) -> int:
    """Run or resume the journaled steps, then print the verify list."""
    folder = _cwd()
    try:
        answers = _load_answers(args, folder)
    except (OSError, ValueError) as exc:
        print(f"not ok: {exc}")
        return 2
    try:
        journal = setup_apply.apply(folder, answers)
    except StepError as exc:
        failed = setup_journal.load(folder) or {}
        print(f"not ok: {exc}")
        if failed.get("failed_step"):
            print(f"Failed step: {failed['failed_step']}. Re-run setup --apply to resume.")
            print("`corpus-rag setup --off` undoes the finished steps.")
        return 1
    print(f"ok: study '{journal.get('slug')}' is set up.")
    for command in setup_steps.verify_list(journal):
        print(f"Verify: {command}")
    return 0


def _off_folder(folder: Path, stop_studies: bool = True) -> list[str]:
    """Undo one folder's setup; returns the lines to print."""
    lines: list[str] = []
    journal = setup_journal.load(folder) or {}
    undos = setup_journal.undo_records(journal)
    # The allow rules are a fixed known set: without a journal record the
    # standard rules come out, so `--all` still works after a lost journal.
    removed = setup_steps.undo_allow_rules(folder, undos.get("allow_rules"))
    if removed:
        lines.append(f"- allow rules removed ({folder}/.claude/settings.json)")
    if "engine_override" in undos:
        setup_steps.undo_engine_override(folder, undos["engine_override"])
        lines.append("- queue.autorun restored in the config overlay")
    if stop_studies:
        slug = journal.get("slug") or gate_state.folder_entry(folder).get("slug")
        if slug:
            lines.extend(_stop_study(slug))
    marker = folder / ".claude" / "corpus.json"
    if marker.is_file():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            data["enabled"] = False
            marker.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            lines.append("- .claude/corpus.json set enabled: false")
    gate_state.update_folder(folder, {"declined": True, "enabled_by_setup": False})
    setup_offer.remove_sentinel(folder)
    lines.append("- offer declined for this folder (the database and journal stay)")
    return lines


def _stop_study(slug: str) -> list[str]:
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


def cmd_setup_off(args) -> int:
    """Undo the folder's setup; ``--all`` covers every setup-enabled folder."""
    if getattr(args, "all", False):
        folders = gate_state.folders_enabled_by_setup()
        if not folders:
            print("No folder was set up by this plugin.")
            return 0
        for folder in folders:
            print(f"Folder: {folder}")
            for line in _off_folder(folder):
                print(line)
        return 0
    folder = _cwd()
    journal = setup_journal.load(folder)
    marker = folder / ".claude" / "corpus.json"
    if journal is None and not marker.is_file():
        print("No setup journal or corpus study here. Nothing to undo.")
        return 1
    for line in _off_folder(folder):
        print(line)
    return 0


