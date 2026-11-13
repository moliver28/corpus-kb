"""Setup orchestration: answers, the slug rule, apply, and resume.

``apply`` runs the four journaled steps in order (issue #353): a retry
skips the steps recorded ``done`` and resumes at the first not-done one;
a failed step records ``failed_step`` and raises ``StepError``. After a
full run the gate state gains ``slug``, ``enabled_by_setup``, and the
consent flag (``local_only``), and the research sentinel goes away.

Slug rule: ``corpus.json`` wins; else the folder name; if another folder
owns that slug (gate state or the study registry), append six hex
characters of the SHA-1 of the folder path.

Stdlib only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import gate_state
import setup_journal
import setup_offer
import study_registry
from setup_steps import RUNNERS, StepError


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("yes", "y", "true", "on", "1")
    return bool(value)


def normalize_answers(answers: dict) -> dict:
    """Header-keyed answers to the canonical apply shape."""
    watch = answers.get("Folders") or answers.get("Watch folders") or ["."]
    if isinstance(watch, str):
        watch = [item.strip() for item in watch.split(",") if item.strip()]
    return {
        "slug": str(answers.get("Study name") or "").strip(),
        "watch": [str(item) for item in watch] or ["."],
        "backend": str(answers.get("Backend") or "").strip() or None,
        "cloud_ok": _truthy(answers.get("Cloud model")),
        "auto_ingest": _truthy(answers.get("Auto ingest")),
        "allow_rules": _truthy(answers.get("Permissions")),
        "autorun": _truthy(answers.get("Autorun")),
        "uxr": _truthy(answers.get("uxr") or answers.get("UXR")),
    }


def slug_for_folder(folder: Path, explicit: str | None = None) -> str:
    """``corpus.json`` wins; else the folder name; else the explicit answer."""
    folder = Path(folder)
    if explicit:
        base = study_registry.slugify(explicit)
    else:
        found = study_registry.folder_study(folder)
        base = study_registry.slugify(found if found else folder.name)
    if not _slug_owned(folder, base):
        return base
    digest = hashlib.sha1(str(folder.resolve()).encode("utf-8")).hexdigest()[:6]
    return f"{base}_{digest}"


def _slug_owned(folder: Path, slug: str) -> bool:
    """True when ANOTHER folder or a study already owns the slug."""
    state = gate_state.load_state()
    own_key = gate_state.folder_key(folder)
    for key, entry in state.get("folders", {}).items():
        if key != own_key and isinstance(entry, dict) and entry.get("slug") == slug:
            return True
    registry = study_registry.load_registry()
    return slug in registry.get("studies", {})


def apply(folder: Path, answers: dict) -> dict:
    """Run or resume the setup. Returns the journal when everything is done.

    Raises ``StepError`` after recording ``failed_step``; a retry skips
    the done steps.
    """
    stored = setup_journal.load(folder)
    effective = answers if answers else (stored.get("answers") if stored else {})
    plan = normalize_answers(effective)
    plan["slug"] = slug_for_folder(folder, plan["slug"])
    journal = stored or setup_journal.new_journal(folder, effective, plan["slug"])
    journal["slug"] = plan["slug"]
    journal["folder"] = str(folder)
    journal["answers"] = effective
    for name in setup_journal.STEP_ORDER:
        if name in setup_journal.done_steps(journal):
            continue
        try:
            undo = RUNNERS[name](folder, plan)
            setup_journal.record_step(journal, name, "done", undo)
        except StepError as exc:
            setup_journal.record_step(journal, name, "failed")
            setup_journal.save(folder, journal)
            raise StepError(
                f"{name} failed: {exc}. Fix it, then re-run setup --apply. "
                "Or run setup --off to undo the finished steps."
            ) from exc
        setup_journal.save(folder, journal)
    _after_apply(folder, plan)
    return journal


def _after_apply(folder: Path, plan: dict) -> None:
    """Gate-state outcome: slug, enabled_by_setup, and the consent flag."""
    gate_state.update_folder(
        folder,
        {
            "slug": plan["slug"],
            "enabled_by_setup": True,
            "local_only": not plan["cloud_ok"],
        },
    )
    setup_offer.remove_sentinel(folder)


def verify_list(journal: dict) -> list[str]:
    """The commands the skill runs after a successful apply."""
    slug = journal.get("slug") or ""
    return [
        f"corpus-rag status -p {slug}",
        f'corpus-rag search "summary" -p {slug}',
        f"corpus-rag drain -p {slug}",
    ]
