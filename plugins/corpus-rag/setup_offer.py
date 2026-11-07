"""The guided setup offer: signal scan, gate checks, once per session.

The offer fires on SessionStart (``startup`` only) and on
UserPromptSubmit when the prompt matches the trigger list. Conditions
(issue #353): the folder holds no ``.claude/corpus.json``, it is not this
engine repo, it is not declined and not snoozed (gate state), and the
signal scan finds transcripts or interview summaries. The signal scan
runs depth 2 within a 0.6 s budget and skips hidden folders,
``node_modules``, ``__pycache__``, and ``venv``.

The offer text tells the agent to finish the user's request first, then
ask one question: set up now, not now (snooze), or never here (decline).
Once per session: the session record carries ``setup_offered``; without
a session id a day mark covers it.

A research folder gets a ``.claude/corpus-research`` sentinel so the
UserPromptSubmit shell guard can let the hook reach Python without a
scan. ``decline`` and ``setup --off`` remove it.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import time
import zlib
from pathlib import Path

import folder_state
import gate_state
import hook_state
import transcripts
import triggers

SIGNAL_DEPTH = 2
SIGNAL_BUDGET_S = 0.6
SIGNAL_DIR_EXCLUDES = ("node_modules", "__pycache__", "venv")
SENTINEL_DIR = ".claude"
SENTINEL_NAME = "corpus-research"
SUMMARY_WORDS = ("interview", "participant", "session", "study", "research", "usability")

OFFER_TEXT = (
    "[corpus-rag] This folder holds research transcripts and has no corpus "
    "study yet.\n"
    "Finish the user's current request first. Then ask one question:\n"
    "set up a study here now,\n"
    "not now (run: corpus-rag snooze — hides this for 7 days), or\n"
    "never here (run: corpus-rag decline).\n"
    "If the user says yes, follow the setup skill: corpus-rag setup --questions."
)


# ---------------------------------------------------------------------------
# Signal scan
# ---------------------------------------------------------------------------


def signal_patterns() -> list[str]:
    """Glob patterns that keep the signal scan inside the budget."""
    out = []
    for name in SIGNAL_DIR_EXCLUDES:
        out.append(f"{name}/**")
        out.append(f"**/{name}/**")
    return out


def is_interview_summary(path: Path) -> bool:
    """A research summary file: a summary stem plus a research word."""
    stem = path.stem.lower()
    if "summar" not in stem:
        return False
    return any(word in stem for word in SUMMARY_WORDS)


def signal_scan(root: Path) -> dict:
    """Scan counts for one folder: files, transcripts, interview summaries.

    Depth 2, 0.6 s budget, hidden folders and dependency folders skipped.
    The scan never reads file contents for the counts (the transcript rule
    that needs a head read applies only to the cap-sized candidate list).
    """
    began = time.monotonic()
    found = transcripts.scan_files(
        Path(root),
        signal_patterns(),
        depth=SIGNAL_DEPTH,
        cap=folder_state.RESEARCH_SCAN_CAP,
        deadline=SIGNAL_BUDGET_S,
    )
    row = {"files_scanned": len(found), "transcripts": 0, "interview_summaries": 0}
    for path in found:
        if is_interview_summary(path):
            row["interview_summaries"] += 1
        elif transcripts.is_transcript(path):
            row["transcripts"] += 1
    row["elapsed_s"] = round(time.monotonic() - began, 3)
    return row


def has_signal(row: dict) -> bool:
    return bool(row.get("transcripts") or row.get("interview_summaries"))


# ---------------------------------------------------------------------------
# Folder candidacy and the sentinel
# ---------------------------------------------------------------------------


def is_engine_repo(folder: Path) -> bool:
    return (Path(folder) / folder_state.ENGINE_SOURCE_DIR).is_dir()


def research_blocker(folder: Path, data: dict | None = None) -> str | None:
    """Why the offer may not fire here, or None when it may."""
    folder = Path(folder)
    if folder_state.read_corpus_json(folder) is not None:
        return "corpus"
    if is_engine_repo(folder):
        return "engine-repo"
    if gate_state.is_declined(folder, data):
        return "declined"
    if gate_state.snoozed_until_today(folder, data):
        return "snoozed"
    return None


def sentinel_path(folder: Path) -> Path:
    return Path(folder) / SENTINEL_DIR / SENTINEL_NAME


def touch_sentinel(folder: Path) -> None:
    try:
        sentinel_path(folder).parent.mkdir(parents=True, exist_ok=True)
        sentinel_path(folder).write_text("", encoding="utf-8")
    except OSError:
        pass


def remove_sentinel(folder: Path) -> None:
    _unlink(sentinel_path(folder))


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def sentinel_present(folder: Path) -> bool:
    return sentinel_path(folder).is_file()


def find_sentinel(start: Path) -> Path | None:
    """The nearest walk-up folder holding the research sentinel."""
    current = Path(start).resolve()
    while True:
        if sentinel_path(current).is_file():
            return current
        if current.parent == current:
            return None
        current = current.parent


# ---------------------------------------------------------------------------
# Once-per-session bookkeeping
# ---------------------------------------------------------------------------


def session_id_of(data: dict) -> str:
    return hook_state.sanitize_session_id(
        str(data.get("session_id") or data.get("sessionId") or "")
    )


def offered_this_session(data: dict, folder: Path) -> bool:
    sid = session_id_of(data)
    if not sid:
        day = time.strftime("%Y%m%d")
        mark = f"setup-offer-{zlib.crc32(str(folder).encode('utf-8'))}-{day}"
        return hook_state.mark_exists(mark)
    session = hook_state.load_session(sid)
    return bool(session and session.get("setup_offered"))


def mark_offered(data: dict, folder: Path) -> None:
    sid = session_id_of(data)
    if not sid:
        day = time.strftime("%Y%m%d")
        mark = f"setup-offer-{zlib.crc32(str(folder).encode('utf-8'))}-{day}"
        hook_state.touch_mark(mark)
        return
    session = hook_state.load_session(sid) or hook_state.fresh_session("", folder)
    session["setup_offered"] = True
    hook_state.save_session(sid, session)


# ---------------------------------------------------------------------------
# The offer
# ---------------------------------------------------------------------------


def offer_for_session_start(data: dict, folder: Path | None = None) -> str | None:
    """The SessionStart offer: startup only, once per session, gate-checked."""
    data = dict(data)
    data.setdefault("hook_event_name", "startup")
    if str(data.get("hook_event_name") or "startup") != "startup":
        return None
    return _offer(data, folder, prompt="")


def offer_for_prompt(data: dict, folder: Path | None = None, prompt: str = "") -> str | None:
    """The UserPromptSubmit offer: trigger match required."""
    if not triggers.matches(prompt):
        return None
    return _offer(data, folder, prompt=prompt)


def _offer(data: dict, folder: Path | None, prompt: str) -> str | None:
    folder = Path(folder) if folder is not None else Path(
        str(data.get("cwd") or Path.cwd())
    )
    blocker = research_blocker(folder)
    if blocker is not None:
        return None
    if offered_this_session(data, folder):
        return None
    counts = signal_scan(folder)
    if not has_signal(counts):
        return None
    mark_offered(data, folder)
    touch_sentinel(folder)
    return OFFER_TEXT
