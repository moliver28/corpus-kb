"""SessionStart hook behavior: the study block, watchPaths, prune (#352, #353).

The block prints slug, document count, last ingest date, the evidence
rules, and the skill list. The count query probes the study port with a
1 s connect timeout and gives the statement a 200 ms budget; a slow or
down engine degrades to "Run `corpus-rag status`". The output carries
``watchPaths`` so FileChanged fires for the watch folders.

Research folders (transcripts, no corpus.json) get the guided setup
offer on the ``startup`` event, once per session, gate-checked (issue
#353). Every call prunes state once per day: sessions older than 30
days out, the two jsonl logs capped at 1 MB. The same event dedups
within 5 s.

Stdlib only.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import folder_state
import hook_events
import hook_state
import op_client
import setup_offer
import study_registry

CONNECT_TIMEOUT_S = 1.0
STATEMENT_TIMEOUT_S = 0.2
COUNT_SQL = "SELECT count(*) AS documents, max(created_at) AS last_ingest FROM documents"

ENGINE_DOWN_LINE = "Engine down. Run `corpus-rag status`."
EVIDENCE_RULES = (
    "Search the corpus before reading transcripts by hand.",
    "Cite every claim as [S:<source-slug>].",
    "analysis/, deliverables/, and *-summary.md are not evidence.",
)
SKILL_LIST = "Skills: /corpus-ask, /corpus-analyze, /corpus-ingest, /corpus-admin, /setup"


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
        hook_events.ensure_session(data, fs)
        return session_start_output(fs)
    if fs.state == folder_state.RESEARCH:
        return setup_offer.offer_for_session_start(data, fs.root)
    return None
