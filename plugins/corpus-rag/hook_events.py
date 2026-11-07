"""Session-level hook behavior: UserPromptSubmit and SubagentStart.

Each ``run_*`` function takes the parsed hook stdin and returns the text
to print, or None to stay silent (issue #352). Every failure path leaves
the session untouched: the hook bodies in ``hooks/`` catch and record.

UserPromptSubmit queues files added outside a tool call (watch-folder
scan, 300 files, depth 4) and keeps the 0.7.0 lane nudges (ask, analyze,
ingest), now keyed per study: one lane per prompt, once per study per
day, unless a lane is declined. It also starts a new turn, which resets
the per-turn read and corpus-call bookkeeping.

SubagentStart gives the subagent three lines and marks the session, so
PreToolUse can skip subagent tool calls.

Shared session helpers live here; ``hook_session`` and ``hook_watch``
import them.

Stdlib only.
"""

from __future__ import annotations

import re
import time
import zlib
from pathlib import Path

import folder_state
import hook_state
import ingest_queue
import transcripts


def declined_mark_for(folder: Path) -> str:
    """The declined mark name for one folder (tests use it too)."""
    return folder_state.declined_mark_name(folder)


def session_id_of(data: dict) -> str:
    raw = str(data.get("session_id") or data.get("sessionId") or "")
    return hook_state.sanitize_session_id(raw)


def ensure_session(data: dict, fs: folder_state.FolderState) -> tuple[str, dict] | None:
    """The session record, created when missing. None without a session id."""
    sid = session_id_of(data)
    if not sid:
        return None
    session = hook_state.load_session(sid) or hook_state.fresh_session(fs.slug or "", fs.root)
    session["last"] = hook_state.now()
    if fs.slug:
        session["slug"] = fs.slug
    hook_state.save_session(sid, session)
    return sid, session


# ---------------------------------------------------------------------------
# UserPromptSubmit
# ---------------------------------------------------------------------------

LANE_PATTERNS = (
    ("analyze", r"which test|significant|correlation|how do i analyze|statistical"),
    ("ingest", r"ingest|\.pdf|\.vtt|\.srt|transcript"),
    (
        "ask",
        r"\?|^what |^why |^how |^when |^where |^who |^which |^can |^does |^is |^are |^did |^do ",
    ),
)
LANE_TEXT = {
    "analyze": (
        "Not sure which analysis fits your question? Run: corpus-kb "
        "analysis-select-method. It names a method and explains why."
    ),
    "ingest": (
        "New files in a watch folder queue themselves. For one file now: "
        "corpus-rag ingest <path> -p {slug}. The corpus-ingest skill has the details."
    ),
    "ask": (
        'Your corpus can answer with citations. Run: corpus-rag ask "<question>" '
        '-p {slug}. To scout first: corpus-rag search "<words>" -p {slug}.'
    ),
}


def scan_mark_name(root: Path) -> str:
    return f"scan-{zlib.crc32(str(root).encode('utf-8'))}"


def scan_queue(fs: folder_state.FolderState) -> None:
    """Queue watch-folder files added or changed outside a tool call."""
    mark = scan_mark_name(fs.root)
    try:
        last = float(hook_state.mark_path(mark).read_text(encoding="utf-8").strip() or 0.0)
    except (OSError, ValueError):
        last = 0.0
    hook_state.touch_mark(mark, stamp=str(time.time()))
    for watch in fs.watch_dirs():
        for path in transcripts.scan_files(watch, fs.exclude):
            try:
                if path.stat().st_mtime > last:
                    ingest_queue.enqueue(fs.root, path)
            except OSError:
                continue


def run_prompt_submit(data: dict) -> str | None:
    cwd = Path(str(data.get("cwd") or Path.cwd()))
    fs = folder_state.folder_state(cwd)
    if fs.state != folder_state.CORPUS:
        return None
    scan_queue(fs)
    found = ensure_session(data, fs)
    if found is not None:
        sid, session = found
        session["turn"] = int(session.get("turn") or 0) + 1
        session["turn_reads"] = []
        session["first_read_at"] = None
        session["corpus_called_at"] = None
        session["corpus_turn"] = None
        session["subagent"] = False
        hook_state.save_session(sid, session)
    return lane_nudge(str(data.get("prompt") or ""), fs)


def lane_nudge(prompt: str, fs: folder_state.FolderState) -> str | None:
    """One lane per prompt, once per study per day, unless declined."""
    for lane, pattern in LANE_PATTERNS:
        if not re.search(pattern, prompt, re.IGNORECASE):
            continue
        if hook_state.mark_exists(f"declined-{lane}"):
            return None
        day = time.strftime("%Y%m%d")
        mark = f"prompt-{fs.slug}-{lane}-{day}"
        if hook_state.mark_exists(mark):
            return None
        hook_state.touch_mark(mark)
        return LANE_TEXT[lane].replace("{slug}", fs.slug or "")
    return None


# ---------------------------------------------------------------------------
# SubagentStart
# ---------------------------------------------------------------------------


def run_subagent_start(data: dict) -> str | None:
    cwd = Path(str(data.get("cwd") or Path.cwd()))
    fs = folder_state.folder_state(cwd)
    if fs.state != folder_state.CORPUS:
        return None
    found = ensure_session(data, fs)
    if found is not None:
        sid, session = found
        session["subagent"] = True
        hook_state.save_session(sid, session)
    slug = fs.slug or ""
    return "\n".join(
        [
            f'Search the corpus first: corpus-rag search "<words>" -p {slug}.',
            f"Study: {slug}.",
            "Evidence is cited as [S:<source-slug>]."
            " analysis/, deliverables/, and *-summary.md are not evidence.",
        ]
    )
