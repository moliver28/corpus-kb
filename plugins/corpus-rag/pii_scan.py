"""The setup PII scan: per-file counts only, never text (issue #353).

The scan reads each transcript and reports counts of email addresses and
person-like names. Output holds paths, kinds, and counts — never the
matched text. Role labels (participant, interviewer, moderator, and a
few close variants) never count as person names, and a labelled line
contributes only its body, not the label.

Stdlib only.
"""

from __future__ import annotations

import re
from pathlib import Path

import transcripts

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NAME_RE = re.compile(r"\b([A-Z][a-z]{1,20})\s+([A-Z][a-z]{1,20})\b")
ROLE_LABELS = (
    "participant",
    "interviewer",
    "moderator",
    "facilitator",
    "speaker",
    "note taker",
    "observer",
    "respondent",
)
KINDS = ("email", "person_name")
MAX_READ_BYTES = 512 * 1024
NAME_STOP = {
    "the",
    "this",
    "that",
    "there",
    "then",
    "and",
    "but",
    "for",
    "not",
    "yes",
    "no",
    "okay",
    "ok",
    "so",
    "it",
    "in",
    "on",
    "we",
    "he",
    "she",
    "they",
    "you",
    "i",
    "if",
    "when",
    "what",
    "why",
    "how",
    "who",
    "um",
    "uh",
}

_LABEL = re.compile(r"^\s*(?:\*\*)?([A-Za-z0-9][\w .'\-]{0,60}?)(?:\*\*)?\s*:")


def _strip_role_label(line: str) -> str:
    """The line body after a role label; role labels never reach the scan."""
    match = _LABEL.match(line)
    if not match:
        return line
    label = match.group(1).strip().lower()
    for role in ROLE_LABELS:
        if label == role or label.startswith(role + " "):
            return line[match.end():]
    return line


def scan_text(text: str) -> dict[str, int]:
    """Counts per kind. Never returns any matched text."""
    emails = 0
    names = 0
    for line in text.splitlines():
        body = _strip_role_label(line)
        emails += len(EMAIL_RE.findall(body))
        for first, second in NAME_RE.findall(body):
            if first.lower() in NAME_STOP or second.lower() in NAME_STOP:
                continue
            if first.lower() in ROLE_LABELS or second.lower() in ROLE_LABELS:
                continue
            names += 1
    return {"email": emails, "person_name": names}


def file_text(path: Path) -> str:
    """The file's text, capped. A .docx scan reads its XML head only."""
    if path.suffix.lower() == ".docx":
        return transcripts.head_text(path)
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_READ_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def scan_file(path: Path) -> dict:
    """One file's row: path, kinds, counts. No text leaves this module."""
    counts = scan_text(file_text(path))
    return {"path": str(path), "counts": counts}


def scan_files(paths: list[Path]) -> list[dict]:
    """Per-file rows for the setup skill to list. Never any text.

    A path that is not a readable file (missing, deleted mid-scan) is
    skipped: it cannot be scanned, so it gets no row.
    """
    rows = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            rows.append(scan_file(path))
        except OSError:
            continue
    return rows


def scan_folder(root: Path, limit: int = 200) -> list[dict]:
    """Scan the transcripts under one folder, capped, sorted by path."""
    found = transcripts.scan_files(
        Path(root),
        list(transcripts.DEFAULT_EXCLUDES),
        depth=transcripts.SCAN_DEPTH,
        cap=limit,
    )
    rows = [row for row in scan_files(found) if any(row["counts"].values())]
    return rows
