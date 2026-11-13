"""Classification rules and speaker parsing for the UXR layer (#355).

Path rules run first: ``raw-transcripts/`` and ``participant-key.md``
are excluded; ``00-overview.md`` and ``*.spec.json`` are the plan;
``analysis/``, ``deliverables/``, ``*-summary.md``, and
``*-key-points.md`` are derived. Everything else sorts by its speakers
against the roster (participants) and the staff list. A file is never
called internal when a line may hide a speaker. No model call.

Stdlib only.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import transcripts

PARTICIPANT = "participant_session"
MEETING = "internal_meeting"
PLAN = "plan"
DERIVED = "derived"
EXCLUDED = "excluded"
UNMATCHED = "unmatched"

# A speaker label: "Dana (PM):" or "**Dana (PM)**:" at a line start. The
# shape mirrors the engine's SPEAKER_LINE but allows parentheses, so role
# marks like "(PM)" stay inside the label.
_SPEAKER = re.compile(r"^\s*(?:\*\*)?([A-Za-z0-9][\w .'()\-]{0,60}?)(?:\*\*)?\s*:")

HEAD_BYTES = 4096


def parse_speakers(text: str) -> tuple[list[str], bool]:
    """The speaker labels in file order, and whether a line may hide one.

    A line with bold markers that the label pattern does not match may
    hide a speaker, so the caller must treat the file as uncertain.
    """
    names: list[str] = []
    seen: set[str] = set()
    uncertain = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _SPEAKER.match(stripped)
        if match:
            label = match.group(1).strip().lower()
            if label and label not in seen:
                seen.add(label)
                names.append(label)
        elif "**" in stripped:
            uncertain = True
    return names, uncertain


def classify_rel(rel: str) -> str | None:
    """The path-rule class for one study-relative posix path, or None."""
    norm = rel.replace("\\", "/").lstrip("/")
    name = norm.rsplit("/", 1)[-1]
    if norm.startswith("raw-transcripts/") or norm.endswith("/raw-transcripts/"):
        return EXCLUDED
    if name == "participant-key.md":
        return EXCLUDED
    if name == "00-overview.md" or fnmatch.fnmatch(name, "*.spec.json"):
        return PLAN
    if (
        norm.startswith("analysis/")
        or norm.startswith("deliverables/")
        or fnmatch.fnmatch(name, "*-summary.md")
        or fnmatch.fnmatch(name, "*-key-points.md")
    ):
        return DERIVED
    return None


def load_names_from_text(text: str) -> set[str]:
    """One name per line; blank lines and ``#`` comments drop out."""
    names: set[str] = set()
    for line in text.splitlines():
        clean = line.strip()
        if clean and not clean.startswith("#"):
            names.add(clean.lower())
    return names


def load_names(path: Path | None) -> set[str]:
    if path is None:
        return set()
    try:
        return load_names_from_text(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return set()


def classify_file(
    root: Path, rel: str, roster: set[str] | None = None, staff: set[str] | None = None
) -> dict:
    """One file's class, speakers, and uncertain flag."""
    path_rule = classify_rel(rel)
    path = root / rel
    names, uncertain = parse_speakers(transcripts.head_text(path))
    row = {"path": rel, "class": "", "speakers": names, "uncertain": uncertain}
    if path_rule is not None:
        row["class"] = path_rule
        return row
    lowered = [name.lower() for name in names]
    if roster and any(name in roster for name in lowered):
        row["class"] = PARTICIPANT
        return row
    if lowered and staff and not uncertain and all(name in staff for name in lowered):
        row["class"] = MEETING
        return row
    if lowered and uncertain and staff and all(name in staff for name in lowered):
        # Every speaker seen is staff, but a line may hide a speaker, so
        # the conservative class is a participant session, never internal.
        row["class"] = PARTICIPANT
        return row
    row["class"] = UNMATCHED
    return row


def classify_dir(
    root: Path,
    roster: set[str] | None = None,
    staff: set[str] | None = None,
) -> list[dict]:
    """Every file under the study root, sorted by the relative path."""
    root = Path(root).resolve()
    found: list[dict] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if path.parent.name == ".claude" or rel.startswith(".claude/"):
            continue
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        found.append(classify_file(root, rel, roster, staff))
    found.sort(key=lambda row: row["path"])
    return found
