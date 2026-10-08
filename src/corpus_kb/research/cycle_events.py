"""Cycle stage/gate events (todo 20) — PINNED schema, offline-tested.

``--json`` emits ONE JSON object per event; every event is validated
against :data:`EVENT_REQUIRED` before emission (fail-loud on schema
drift). Human mode renders the same events as narration; every printed
prose string comes from guide_copy (the sole prose source).
"""

from __future__ import annotations

import json
from typing import Any

from corpus_kb.research import guide_copy

EVENT_CYCLE_STARTED = "cycle_started"
EVENT_STAGE_STARTED = "stage_started"
EVENT_STAGE_COMPLETED = "stage_completed"
EVENT_STAGE_UNAVAILABLE = "stage_unavailable"
EVENT_GATE_RAISED = "gate_raised"
EVENT_CYCLE_HALTED = "cycle_halted"
EVENT_AWAITING_APPROVAL = "awaiting_approval"
EVENT_APPROVAL_DENIED = "approval_denied"
EVENT_CYCLE_COMPLETED = "cycle_completed"

EVENT_NAMES: frozenset[str] = frozenset(
    {
        EVENT_CYCLE_STARTED,
        EVENT_STAGE_STARTED,
        EVENT_STAGE_COMPLETED,
        EVENT_STAGE_UNAVAILABLE,
        EVENT_GATE_RAISED,
        EVENT_CYCLE_HALTED,
        EVENT_AWAITING_APPROVAL,
        EVENT_APPROVAL_DENIED,
        EVENT_CYCLE_COMPLETED,
    }
)

EVENT_REQUIRED: dict[str, tuple[str, ...]] = {
    EVENT_CYCLE_STARTED: ("run_id", "mode", "stages"),
    EVENT_STAGE_STARTED: ("run_id", "stage"),
    EVENT_STAGE_COMPLETED: ("run_id", "stage", "receipt"),
    EVENT_STAGE_UNAVAILABLE: ("run_id", "stage", "reason"),
    EVENT_GATE_RAISED: ("run_id", "gate", "reason"),
    EVENT_CYCLE_HALTED: ("run_id", "gate", "exit_code", "action"),
    EVENT_AWAITING_APPROVAL: ("run_id", "stage"),
    EVENT_APPROVAL_DENIED: ("run_id", "stage"),
    EVENT_CYCLE_COMPLETED: ("run_id", "stages"),
}


def validate_cycle_event(event: dict[str, object]) -> list[str]:
    """Missing-field problems for one candidate event (empty = valid)."""
    name = event.get("event")
    if name not in EVENT_NAMES:
        return [f"unknown event name: {name!r}"]
    required = EVENT_REQUIRED[str(name)]
    return [f"missing field: {field}" for field in required if event.get(field) is None]


def cycle_event(name: str, **fields: object) -> dict[str, object]:
    """Build one event; raises ValueError on any schema violation."""
    event: dict[str, object] = {"event": name, **fields}
    problems = validate_cycle_event(event)
    if problems:
        raise ValueError(f"cycle event schema violation ({name}): {problems}")
    return event


def bounded_receipt(receipt: dict[str, Any]) -> dict[str, object]:
    """JSON-safe event view of a stage receipt: scalars + small flat data."""
    bounded: dict[str, object] = {}
    for key, value in receipt.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            bounded[key] = value
        elif isinstance(value, list) and len(value) <= 12:
            bounded[key] = [v for v in value if isinstance(v, (str, int, float, bool))]
        elif isinstance(value, dict):
            bounded[key] = {
                k: v for k, v in list(value.items())[:12] if isinstance(v, (str, int, float, bool))
            }
    return bounded


class CycleRecorder:
    """Validates, accumulates, and renders the cycle's event stream."""

    def __init__(self, json_output: bool = False) -> None:
        self.json_output = json_output
        self.events: list[dict[str, object]] = []

    def emit(self, name: str, **fields: object) -> dict[str, object]:
        event = cycle_event(name, **fields)
        self.events.append(event)
        if self.json_output:
            print(json.dumps(event, default=str))
        else:
            render_event(event)
        return event


def render_event(event: dict[str, object]) -> None:
    """Human narration for one event (guide_copy is the sole prose source)."""
    name = str(event["event"])
    if name == EVENT_STAGE_STARTED:
        print(f"--- {event.get('stage')} ---")
    elif name == EVENT_STAGE_COMPLETED:
        print(f"done: {event.get('stage')}")
        receipt = event.get("receipt")
        if isinstance(receipt, dict) and receipt:
            print(f"receipt: {json.dumps(receipt, default=str)}")
    elif name == EVENT_STAGE_UNAVAILABLE:
        print(f"unavailable: {event.get('stage')} - {event.get('reason')}")
    elif name == EVENT_GATE_RAISED:
        print(f"gate raised: {event.get('gate')} - {event.get('reason')}")
    elif name == EVENT_CYCLE_HALTED:
        print(guide_copy.CYCLE_HALT_HEADER.format(gate=event.get("gate")))
        print(f"action: {event.get('action')} (exit {event.get('exit_code')})")
    elif name == EVENT_APPROVAL_DENIED:
        print(guide_copy.CYCLE_APPROVAL_DENIED)
    elif name == EVENT_CYCLE_COMPLETED:
        print(guide_copy.CYCLE_OUTRO)
