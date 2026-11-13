"""UXR hook behavior: detection, session lines, prompt nudges (#355).

A study is UXR when ``corpus.json`` has ``uxr.ticket``, or
``00-overview.md`` names a ticket, or a ``<PREFIX>-N.spec.json`` file
sits at the root. Hooks only read: the SessionStart block gains UXR
lines, and UserPromptSubmit nudges once per set or injects the skill
path once per session. A hook never runs the analysis and never writes
a study store.

Stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import hook_state
import ingest_queue
import uxr_store

TICKET = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
SPEC_NAME = re.compile(r"^([A-Z][A-Z0-9]+)-(\d+)\.spec\.json$")
PHRASES = (
    re.compile(r"what did we learn", re.IGNORECASE),
    re.compile(r"is h\d+ supported", re.IGNORECASE),
    re.compile(r"are the hypotheses", re.IGNORECASE),
    re.compile(r"theme summary", re.IGNORECASE),
)
HEAD_BYTES = 4096

QUEUE_MARK = "uxr-queue-{sid}"
ANALYSIS_MARK = "uxr-analysis-{sid}"
PHRASE_MARK = "uxr-phrase-{sid}"

SESSION_LINES = (
    "UXR study {ticket}. Stance rows are model output; check every quote "
    "against the transcript before you use it.",
    "Retrieve first with `corpus-rag uxr insights`; analyze with the corpus-uxr skill.",
)
SKILL_SUFFIX = ", /corpus-uxr"


def is_uxr(root: Path, marker_data: dict | None = None) -> str | None:
    """The study's ticket when the study is a UXR study, else None."""
    root = Path(root)
    data = marker_data
    if data is None:
        try:
            data = json.loads((root / ".claude" / "corpus.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    if isinstance(data, dict):
        nested = data.get("uxr")
        ticket = nested.get("ticket") if isinstance(nested, dict) else None
        if not ticket:
            ticket = data.get("uxr.ticket")
        if isinstance(ticket, str) and ticket.strip():
            return ticket.strip()
    overview = root / "00-overview.md"
    try:
        head = overview.read_text(encoding="utf-8")[:HEAD_BYTES]
    except OSError:
        head = ""
    match = TICKET.search(head)
    if match:
        return match.group(1)
    try:
        for entry in sorted(root.iterdir()):
            spec = SPEC_NAME.match(entry.name)
            if spec and entry.is_file():
                # The ticket is the file's prefix-N stem, like UX1-42.
                return entry.name.removesuffix(".spec.json")
    except OSError:
        return None
    return None


def session_lines(ticket: str) -> list[str]:
    """The UXR lines added to the SessionStart study block."""
    return [line.format(ticket=ticket) for line in SESSION_LINES]


def extend_skill_list(skills_line: str) -> str:
    """One more skill on the pinned skill line."""
    return skills_line + SKILL_SUFFIX


# ---------------------------------------------------------------------------
# UserPromptSubmit
# ---------------------------------------------------------------------------


def phrase_hit(prompt: str) -> bool:
    """True when the prompt holds a UXR phrase."""
    return any(pattern.search(prompt) for pattern in PHRASES)


def skill_path() -> str:
    return str(Path(__file__).resolve().parent / "skills" / "corpus-uxr" / "SKILL.md")


def prompt_nudge(
    prompt: str,
    slug: str | None,
    root: Path,
    sid: str,
    marker_data: dict | None = None,
) -> str | None:
    """The UXR nudge for this prompt, or None. UXR studies only.

    Order: the skill-path injection (once per session), then the queue
    nudge, then the not-analyzed nudge. Each fires once per session.
    """
    if not slug or is_uxr(root, marker_data) is None:
        return None
    key = hook_state.sanitize_session_id(sid) or "nosess"
    if phrase_hit(prompt):
        mark = PHRASE_MARK.format(sid=key)
        if not hook_state.mark_exists(mark):
            hook_state.touch_mark(mark)
            return (
                "This is a UXR study. Retrieve what is stored before any new "
                f"analysis: run the corpus-uxr skill ({skill_path()}) and "
                f"`corpus-rag uxr insights -p {slug}`."
            )
    pending = ingest_queue.queue_rows(root)
    if pending:
        mark = QUEUE_MARK.format(sid=key)
        if not hook_state.mark_exists(mark):
            hook_state.touch_mark(mark)
            return (
                f"{len(pending)} file(s) sit in the ingest queue. Run: "
                f"`corpus-rag drain -p {slug}` before analysis."
            )
    return analysis_nudge(slug, key)


def analysis_nudge(slug: str, key: str) -> str | None:
    """One nudge when confirmed sessions have no stored rows yet."""
    mark = ANALYSIS_MARK.format(sid=key)
    if hook_state.mark_exists(mark):
        return None
    try:
        conn = uxr_store.connect_existing(slug)
        if conn is None:
            return None
        confirmed = uxr_store.confirmed_sessions(conn)
        analyzed = uxr_store.sessions_with_rows(conn)
    except (OSError, ValueError):
        return None
    if not (confirmed - analyzed):
        return None
    hook_state.touch_mark(mark)
    return (
        "Sessions are ingested but not analyzed yet. The corpus-uxr skill "
        f"({skill_path()}) has the flow."
    )
