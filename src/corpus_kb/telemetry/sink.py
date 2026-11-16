"""The JSONL event sink (issue #442): append-only, whitelisted, never raises.

One file per log directory (``events.jsonl``), one JSON object per line.
The line carries ONLY the six whitelisted event keys plus
``schema_version`` — everything else is discarded at write time, so no
corpus content, query text, or error message can ever reach the log.

Failure policy (owner-confirm F5): a write that fails (full disk, locked
file, vanished folder) is swallowed after flipping the in-process
``_suppressed`` flag — the operation the event describes never crashes
because telemetry failed. The flag lives ONLY in the emitting process;
the report script runs in a separate process and can never see it.
Suppressed events leave no trace anywhere, so suppression is
indistinguishable from no activity. That loss is accepted and stated:
telemetry is opt-in, confirmation-only, and never worth breaking an op.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EVENT_SCHEMA_VERSION = "1"

# The six event keys (the #441 meter vocabulary) plus the schema tag.
_WHITELIST: tuple[str, ...] = (
    "method_id",
    "success",
    "outcome",
    "duration",
    "first_attempt",
    "session_ordinal",
)

# In-process only (see module docstring): flipped by the first dropped
# write. Never persisted, never read across processes.
_suppressed: bool = False


@dataclass(frozen=True)
class Event:
    """One method-invocation event; every field is metadata, none is content."""

    method_id: str
    success: bool
    outcome: str
    duration: int  # integer milliseconds around the handler call
    first_attempt: bool
    session_ordinal: int
    schema_version: str = EVENT_SCHEMA_VERSION


def event_line(event: Event) -> str:
    """The deterministic JSONL line: whitelisted keys, sorted, one line."""
    payload = {key: getattr(event, key) for key in (*_WHITELIST, "schema_version")}
    return json.dumps(payload, sort_keys=True)


def write_event(path: Path, event: Event) -> None:
    """Append one whitelisted JSONL line; create the folder on first write.

    Never raises: ``OSError``/``ValueError`` inside the writer are
    suppressed and flip ``_suppressed`` (F5). The caller writes only when
    telemetry is enabled, so directory creation happens on the first
    enabled write only.
    """
    global _suppressed
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(event_line(event) + "\n")
    except (OSError, ValueError, TypeError):
        _suppressed = True


def suppression_flag() -> bool:
    """True once a write was dropped in this process (a debug aid only)."""
    return _suppressed
