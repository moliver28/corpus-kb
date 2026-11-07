"""Session-level hook behavior: SessionStart, UserPromptSubmit, SubagentStart.

Each ``run_*`` function takes the parsed hook stdin and returns the text
to print, or None to stay silent (issue #352). Every failure path leaves
the session untouched: the hook bodies in ``hooks/`` catch and record.

SessionStart prints the study block (slug, document count, last ingest
date, evidence rules, skill list) and returns ``watchPaths`` so
FileChanged fires. The count query probes the study port with a 1 s
connect timeout and gives the statement a 200 ms budget; a slow or down
engine degrades to "Run `corpus-rag status`".

UserPromptSubmit queues files added outside a tool call (watch-folder
scan, 300 files, depth 4) and keeps the 0.7.0 lane nudges (ask, analyze,
ingest), now keyed per study.

SubagentStart gives the subagent three lines and marks the session, so
PreToolUse can skip subagent tool calls.

Stdlib only.
"""

from __future__ import annotations

import json
import socket
import time
import zlib
from pathlib import Path

import folder_state
import hook_state
import ingest_queue
import op_client
import study_registry
import transcripts

CONNECT_TIMEOUT_S = 1.0
STATEMENT_TIMEOUT_S = 0.2
COUNT_SQL = "SELECT count(*) AS documents, max(created_at) AS last_ingest FROM documents"

ENGINE_DOWN_LINE = "Engine down. Run `corpus-rag status`."
EVIDENCE_RULES = (
    "Search the corpus before reading transcripts by hand.",
    "Cite every claim as [S:<source-slug>].",
    "analysis/, deliverables/, and *-summary.md are not evidence.",
)
SKILL_LIST = "Skills: /corpus-ask, /corpus-analyze, /corpus-ingest, /corpus-admin"

RESEARCH_OFFER = (
    "This folder holds interview transcripts but has no corpus study yet.\n"
    "Run `corpus-rag provision <slug>` to start one, then ingest the\n"
    "transcripts with `corpus-rag ingest <folder>`. The corpus-ingest skill\n"
    "has the exact steps. Run `corpus-rag use <slug>` once to set it active."
)

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


def declined_mark_for(folder: Path) -> str:
    """The declined mark name for one folder (tests use it too)."""
    return folder_state.declined_mark_name(folder)


def session_id_of(data: dict) -> str:
    return hook_state.sanitize_session_id(str(data.get("session_id") or data.get("sessionId") or ""))


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
# SessionStart
# ---------------------------------------------------------------------------


def probe_connect(port: int, timeout: float = CONNECT_TIMEOUT_S) -> bool:
    """True when the study port answers within the connect timeout."""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def fetch_counts(study: dict) -> dict | None:
    """Document count and last ingest from the study server, or None."""
    port = int(study.get("port") or 0)
    if not port or not probe_connect(port):
        return None
    base = f"http://127.0.0.1:{port}"
    try:
        envelope = op_client.post_json(
            base + "/api/query/sql", {"sql": COUNT_SQL}, timeout=STATEMENT_TIMEOUT_S
        )
    except op_client.HttpError:
        envelope = None
    rows = (envelope or {}).get("result")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        documents = rows[0].get("documents")
        if documents is not None:
            return {"documents": documents, "last_ingest": rows[0].get("last_ingest")}
    try:
        stats = op_client.get_json(base + "/api/stats", timeout=STATEMENT_TIMEOUT_S)
    except op_client.HttpError:
        return None
    if stats.get("documents") is None:
        return None
    return {"documents": stats["documents"], "last_ingest": None}


def counts_line(study: dict | None) -> str:
    if study is None:
        return ENGINE_DOWN_LINE
    counts = fetch_counts(study)
    if counts is None:
        return ENGINE_DOWN_LINE
    last = str(counts.get("last_ingest") or "")[:10] or "never"
    return f"Documents: {counts['documents']} (last ingest {last})"


def study_block(fs: folder_state.FolderState) -> str:
    registry = study_registry.load_registry()
    study = registry.get("studies", {}).get(fs.slug or "")
    lines = [f"Study: {fs.slug}", counts_line(study), *EVIDENCE_RULES, SKILL_LIST]
    return "\n".join(lines)


def session_start_output(fs: folder_state.FolderState) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": study_block(fs),
            },
            "watchPaths": [str(path) for path in fs.watch_dirs()],
        }
    )


def run_session_start(data: dict) -> str | None:
    cwd = Path(str(data.get("cwd") or Path.cwd()))
    event = str(data.get("hook_event_name") or data.get("event") or "startup")
    hook_state.prune_once()
    if hook_state.dedup_hit(f"sessionstart-{event}"):
        return None
    fs = folder_state.folder_state(cwd)
    if fs.state == folder_state.CORPUS:
        ensure_session(data, fs)
        return session_start_output(fs)
    if fs.state == folder_state.RESEARCH:
        day = time.strftime("%Y%m%d")
        mark = f"research-offer-{zlib.crc32(str(fs.root).encode('utf-8'))}-{day}"
        if hook_state.mark_exists(mark):
            return None
        hook_state.touch_mark(mark)
        return RESEARCH_OFFER
    return None


# ---------------------------------------------------------------------------
# UserPromptSubmit
# ---------------------------------------------------------------------------


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
    import re

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
            "Evidence is cited as [S:<source-slug>]. analysis/, deliverables/, and *-summary.md are not evidence.",
        ]
    )
