"""Tool and turn hooks: PreToolUse, PostToolUse, FileChanged, Stop.

PreToolUse nudges when the agent reads a watched transcript by hand
(Read, Grep, or a shell reader such as cat, grep, rg, or sed -n). It
never blocks, nudges at most 3 times per session, skips subagent tool
calls, and stays quiet once this turn already called the corpus. The
detection work holds a 0.5 s deadline.

PostToolUse and FileChanged queue written or changed files in a watch
folder. They skip dotfiles, ``.tmp``, ``.swp``, editor backups (``~``),
and the ``.claude`` state folder; FileChanged ignores ``unlink``.

Stop logs a turn that read a watched transcript before any corpus call
into ``telemetry.jsonl`` and prints nothing. It is the cheapest hook:
no session record, no work.

Stdlib only.
"""

from __future__ import annotations

import shlex
import time
from pathlib import Path

import folder_state
import hook_events
import hook_state
import ingest_queue
import transcripts

NUDGE_CAP = 3
DETECT_DEADLINE_S = 0.5
SHELL_READERS = frozenset({"cat", "grep", "rg", "sed"})
CORPUS_MARKS = ("corpus-rag", "corpus-kb")
TELEMETRY_KIND = "manual_read"


def _tool_input(data: dict) -> dict:
    raw = data.get("tool_input") or data.get("toolInput") or {}
    return raw if isinstance(raw, dict) else {}


def _is_junk(path: Path) -> bool:
    """Dotfiles, temp files, swap files, editor backups, .claude state."""
    if ".claude" in path.parts:
        return True
    name = path.name
    if name.startswith("."):
        return True
    return name.endswith((".tmp", ".swp", "~"))


def _relative_ok(fs: folder_state.FolderState, path: Path) -> bool:
    """True when the path sits in a watch folder and passes the excludes."""
    try:
        rel = path.resolve().relative_to(fs.root.resolve()).as_posix()
    except ValueError:
        return False
    return not transcripts.excluded(rel, fs.exclude)


def _watched_transcript(fs: folder_state.FolderState, path: Path) -> Path | None:
    """The transcript path when it is watched and not excluded."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if _is_junk(resolved):
        return None
    if not any(_under(resolved, watch) for watch in fs.watch_dirs()):
        return None
    if not _relative_ok(fs, resolved):
        return None
    if not transcripts.is_transcript(resolved):
        return None
    return resolved


def _under(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder.resolve())
    except (ValueError, OSError):
        return False
    return True


def _tokens(command: str) -> list[str]:
    """Shell words with quotes stripped and backslashes intact.

    ``shlex`` in POSIX mode eats the backslashes of unquoted Windows
    paths, so this parses in non-POSIX mode and strips the quotes here.
    """
    try:
        raw = shlex.split(command, posix=False)
    except ValueError:
        return []
    out = []
    for token in raw:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
            token = token[1:-1]
        out.append(token)
    return out


def _from_tool_call(fs: folder_state.FolderState, data: dict, deadline: float) -> Path | None:
    """The watched transcript a Read/Grep/Bash call would read by hand."""
    tool = str(data.get("tool_name") or "")
    tool_input = _tool_input(data)
    candidates: list[Path] = []
    if tool in ("Read", "Grep"):
        raw = tool_input.get("file_path") or tool_input.get("path") or ""
        if raw:
            candidates.append(Path(str(raw)))
    elif tool == "Bash":
        command = str(tool_input.get("command") or "")
        if any(mark in command for mark in CORPUS_MARKS):
            return None
        for token in _tokens(command)[1:]:
            if not token or token.startswith("-"):
                continue
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = Path(str(data.get("cwd") or Path.cwd())) / candidate
            candidates.append(candidate)
    for candidate in candidates:
        if time.monotonic() > deadline:
            return None
        found = _watched_transcript(fs, candidate)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# PreToolUse
# ---------------------------------------------------------------------------


def run_pre_tool_use(data: dict) -> str | None:
    cwd = Path(str(data.get("cwd") or Path.cwd()))
    fs = folder_state.folder_state(cwd)
    if fs.state != folder_state.CORPUS:
        return None
    sid = hook_events.session_id_of(data)
    session = hook_state.load_session(sid)
    if session is None:
        return None  # no session record: the turn never started here
    deadline = time.monotonic() + DETECT_DEADLINE_S
    tool = str(data.get("tool_name") or "")
    tool_input = _tool_input(data)
    if tool == "Bash" and any(mark in str(tool_input.get("command") or "") for mark in CORPUS_MARKS):
        session["corpus_called_at"] = hook_state.now()
        session["corpus_turn"] = session.get("turn")
        hook_state.save_session(sid, session)
        return None
    if session.get("subagent"):
        return None
    path = _from_tool_call(fs, data, deadline)
    if path is None:
        return None
    reads = list(session.get("turn_reads") or [])
    if str(path) not in reads:
        reads.append(str(path))
    session["turn_reads"] = reads
    if session.get("first_read_at") is None:
        session["first_read_at"] = hook_state.now()
    nudge = None
    corpus_this_turn = session.get("corpus_turn") is not None and session.get(
        "corpus_turn"
    ) == session.get("turn")
    if int(session.get("nudges") or 0) < NUDGE_CAP and not corpus_this_turn:
        session["nudges"] = int(session.get("nudges") or 0) + 1
        nudge = (
            "You are reading a transcript by hand. Search instead: "
            f'corpus-rag search "<words>" -p {fs.slug}.'
        )
    hook_state.save_session(sid, session)
    return nudge


# ---------------------------------------------------------------------------
# PostToolUse / FileChanged
# ---------------------------------------------------------------------------


def _queue_if_watched(fs: folder_state.FolderState, raw: object) -> None:
    if not raw:
        return
    path = _watched_transcript_or_file(fs, Path(str(raw)))
    if path is not None:
        ingest_queue.enqueue(fs.root, path)


def _watched_transcript_or_file(fs: folder_state.FolderState, path: Path) -> Path | None:
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if _is_junk(resolved) or not resolved.is_file():
        return None
    if not any(_under(resolved, watch) for watch in fs.watch_dirs()):
        return None
    if not _relative_ok(fs, resolved):
        return None
    return resolved


def run_post_tool_use(data: dict) -> str | None:
    cwd = Path(str(data.get("cwd") or Path.cwd()))
    fs = folder_state.folder_state(cwd)
    if fs.state != folder_state.CORPUS:
        return None
    _queue_if_watched(fs, _tool_input(data).get("file_path"))
    return None


def run_file_changed(data: dict) -> str | None:
    kind = str(data.get("kind") or data.get("event") or data.get("action") or "change")
    if kind == "unlink":
        return None
    cwd = Path(str(data.get("cwd") or Path.cwd()))
    fs = folder_state.folder_state(cwd)
    if fs.state != folder_state.CORPUS:
        return None
    raw = data.get("path") or data.get("file_path") or data.get("file")
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_file():  # a change whose file is already gone: treat as unlink
        return None
    _queue_if_watched(fs, path)
    return None


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


def run_stop(data: dict) -> str | None:
    sid = hook_events.session_id_of(data)
    session = hook_state.load_session(sid)
    if session is None:
        return None
    first_read = session.get("first_read_at")
    corpus_at = session.get("corpus_called_at")
    if first_read is not None and (corpus_at is None or corpus_at > first_read):
        hook_state.append_jsonl(
            hook_state.plugin_data_dir() / "telemetry.jsonl",
            {
                "ts": hook_state.now(),
                "session": sid,
                "slug": session.get("slug"),
                "kind": TELEMETRY_KIND,
                "paths": list(session.get("turn_reads") or []),
            },
        )
    session["turn_reads"] = []
    session["first_read_at"] = None
    session["corpus_called_at"] = None
    session["subagent"] = False
    hook_state.save_session(sid, session)
    return None
