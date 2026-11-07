"""Turn-boundary hook behavior: PostToolUse, FileChanged, Stop (issue #352).

PostToolUse and FileChanged queue written or changed files in a watch
folder. They skip dotfiles, ``.tmp``, ``.swp``, editor backups (``~``),
and the ``.claude`` state folder; FileChanged ignores ``unlink``.

Stop logs a turn that read a watched transcript before any corpus call
into ``telemetry.jsonl`` and prints nothing. It is the cheapest hook:
no session record, no work. It also clears the subagent mark, so the
skip window ends with the turn.

Stdlib only.
"""

from __future__ import annotations

from pathlib import Path

import folder_state
import hook_state
import ingest_queue
from hook_watch import is_junk, relative_ok, tool_input, under

TELEMETRY_KIND = "manual_read"


def watched_file(fs: folder_state.FolderState, path: Path) -> Path | None:
    """The path when it is a real file in a watch folder, not junk."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if is_junk(resolved) or not resolved.is_file():
        return None
    if not any(under(resolved, watch) for watch in fs.watch_dirs()):
        return None
    if not relative_ok(fs, resolved):
        return None
    return resolved


def _queue_if_watched(fs: folder_state.FolderState, raw: object) -> None:
    if not raw:
        return
    path = watched_file(fs, Path(str(raw)))
    if path is not None:
        ingest_queue.enqueue(fs.root, path)


def run_post_tool_use(data: dict) -> str | None:
    cwd = Path(str(data.get("cwd") or Path.cwd()))
    fs = folder_state.folder_state(cwd)
    if fs.state != folder_state.CORPUS:
        return None
    _queue_if_watched(fs, tool_input(data).get("file_path"))
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


def run_stop(data: dict) -> str | None:
    from hook_events import session_id_of

    sid = session_id_of(data)
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
