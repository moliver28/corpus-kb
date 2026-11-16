"""Opt-in, confirmation-only local telemetry (issue #442).

One emission seam: the analysis-op adapters (CLI ``flow``, HTTP
``_respond``, the socket arms, the MCP wrapper) call :func:`emit` around
the shared handler. When ``telemetry.enabled`` is ``false`` (the shipped
default) nothing is written. Events carry metadata only: method id,
outcome class, duration, and session counters — no content, no queries,
no network. The JSONL log is read back by ``scripts/telemetry_report.py``,
which CONFIRMS the derived ladder and flags divergence for human review.
It never writes register rows, rungs, or order.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from corpus_kb.telemetry.session import SessionTracker, shared_tracker
from corpus_kb.telemetry.settings import TelemetrySettings, load_telemetry_settings
from corpus_kb.telemetry.sink import EVENT_SCHEMA_VERSION, Event, write_event

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "Event",
    "SessionTracker",
    "TelemetrySettings",
    "emit",
    "shared_tracker",
    "write_event",
]

# Honest no-op statuses: the op answered, there is nothing to fix. The
# same set the CLI exit mapping uses (cli/_plumbing.py).
_OK_STATUSES = ("success", "disabled", "empty", "insufficient_data")

_LOG_FILE_NAME = "events.jsonl"


async def emit(
    handler: Callable[..., Awaitable[dict[str, Any]]],
    *,
    method_id: str | None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Call one analysis handler; record one event when the log is on.

    The ONE wrapper all four adapters use. Returns the handler's envelope
    untouched and ALWAYS re-raises the original error after recording the
    failed event, so each adapter's own error path stays untouched.

    Skip rules: ``telemetry.enabled`` false writes nothing; ``method_id
    is None`` writes NOTHING — no ``Event`` is constructed, no line lands
    (owner-confirm F6), so the log stays register-only. ``first_attempt``
    is read before the call; ``bump()`` happens exactly once, after the
    call; the ordinal it returns is the ``session_ordinal`` (F4).
    """
    settings = load_settings(config)
    mid: str | None = method_id if settings.enabled else None
    tracker = shared_tracker()
    first_attempt = tracker.first_attempt(mid) if mid is not None else False
    started = time.perf_counter() if mid is not None else 0.0
    try:
        result = await handler(**kwargs)
    except Exception as exc:
        if mid is not None:
            _record(settings, tracker, mid, first_attempt, False, outcome_of(exc), started)
        raise
    ok = isinstance(result, dict) and result.get("status") in _OK_STATUSES
    if mid is not None:
        _record(settings, tracker, mid, first_attempt, ok, "ok" if ok else "error", started)
    return result


def load_settings(config: dict[str, Any] | None) -> TelemetrySettings:
    """The telemetry settings for one call; None reads the process config."""
    if config is not None:
        return load_telemetry_settings(config)
    from corpus_kb.config import load_config

    return load_telemetry_settings(load_config())


def outcome_of(exc: BaseException) -> str:
    """The error-taxonomy class of ``exc``, or ``"error"`` — never the message."""
    error_type = getattr(exc, "error_type", None)
    return error_type if isinstance(error_type, str) else "error"


def _record(
    settings: TelemetrySettings,
    tracker: SessionTracker,
    method_id: str,
    first_attempt: bool,
    success: bool,
    outcome: str,
    started: float,
) -> None:
    """One event: the bump returns the ordinal; the sink whitelists and never raises."""
    duration_ms = int((time.perf_counter() - started) * 1000)
    event = Event(
        method_id=method_id,
        success=success,
        outcome=outcome,
        duration=duration_ms,
        first_attempt=first_attempt,
        session_ordinal=tracker.bump(method_id),
    )
    write_event(settings.log_dir / _LOG_FILE_NAME, event)
