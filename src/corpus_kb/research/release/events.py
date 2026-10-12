"""Release lifecycle events — schema-pinned constants + validation.

Naming/serialization follow ``research/cycle_events.py`` exactly: a pinned
name->required-fields table, ``validate_release_event`` returning the missing
field problems (empty = valid), and ``release_event`` building one validated
event dict (raises ValueError on schema drift). The eventsourcing aggregate
methods in ``domain/codebook.py`` carry the same names as their ``@event``
types; this module is the schema contract for the JSON event view.
"""

from __future__ import annotations

EVENT_RELEASE_REQUESTED = "CodebookReleaseRequested"
EVENT_GATE_EVALUATED = "GateEvaluated"
EVENT_WAIVER_RECORDED = "WaiverRecorded"
EVENT_RELEASED = "CodebookReleased"
EVENT_SUPERSEDED = "CodebookSuperseded"
EVENT_RETIRED = "CodebookRetired"
EVENT_CHANGE_PROPOSED = "CodebookChangeProposed"
EVENT_RUBRIC_RECORDED = "ReviewerRubricRecorded"

EVENT_NAMES: frozenset[str] = frozenset(
    {
        EVENT_RELEASE_REQUESTED,
        EVENT_GATE_EVALUATED,
        EVENT_WAIVER_RECORDED,
        EVENT_RELEASED,
        EVENT_SUPERSEDED,
        EVENT_RETIRED,
        EVENT_CHANGE_PROPOSED,
        EVENT_RUBRIC_RECORDED,
    }
)

EVENT_REQUIRED: dict[str, tuple[str, ...]] = {
    EVENT_RELEASE_REQUESTED: (
        "tenant_id",
        "release_id",
        "codebook_id",
        "profile",
        "creator",
        "manifest_sha256",
    ),
    EVENT_GATE_EVALUATED: (
        "tenant_id",
        "release_id",
        "gate_id",
        "status",
        "evaluated_at",
    ),
    EVENT_WAIVER_RECORDED: (
        "tenant_id",
        "release_id",
        "gate_id",
        "justification",
        "approver",
    ),
    EVENT_RELEASED: (
        "tenant_id",
        "release_id",
        "approver",
        "released_at",
    ),
    EVENT_SUPERSEDED: (
        "tenant_id",
        "release_id",
        "superseded_by_release_id",
        "actor",
        "superseded_at",
    ),
    EVENT_RETIRED: (
        "tenant_id",
        "release_id",
        "actor",
        "retired_at",
    ),
    EVENT_CHANGE_PROPOSED: (
        "tenant_id",
        "release_id",
        "successor_release_id",
        "summary",
        "proposed_by",
        "proposed_at",
    ),
    EVENT_RUBRIC_RECORDED: (
        "tenant_id",
        "proposal_id",
        "reviewer",
        "verdict",
        "recorded_at",
    ),
}

RUBRIC_VERDICTS: frozenset[str] = frozenset(
    {"agreement", "reasonable_alternative", "not_reasonable"}
)

GATE_STATUSES: frozenset[str] = frozenset({"pass", "fail", "not_evaluable", "waived"})

RELEASE_STATES: frozenset[str] = frozenset({"draft_candidate", "released", "superseded", "retired"})


def validate_release_event(event: dict[str, object]) -> list[str]:
    """Missing-field / bad-enum problems for one candidate event."""
    name = event.get("event")
    if name not in EVENT_NAMES:
        return [f"unknown event name: {name!r}"]
    required = EVENT_REQUIRED[str(name)]
    problems = [f"missing field: {field}" for field in required if event.get(field) is None]
    status = event.get("status")
    if name == EVENT_GATE_EVALUATED and status not in GATE_STATUSES:
        problems.append(f"invalid gate status: {status!r}")
    verdict = event.get("verdict")
    if name == EVENT_RUBRIC_RECORDED and verdict not in RUBRIC_VERDICTS:
        problems.append(f"invalid rubric verdict: {verdict!r}")
    if name == EVENT_WAIVER_RECORDED and not str(event.get("justification", "")).strip():
        problems.append("empty field: justification")
    if name == EVENT_WAIVER_RECORDED and not str(event.get("approver", "")).strip():
        problems.append("empty field: approver")
    return problems


def release_event(name: str, **fields: object) -> dict[str, object]:
    """Build one release event; raises ValueError on any schema violation."""
    event: dict[str, object] = {"event": name, **fields}
    problems = validate_release_event(event)
    if problems:
        raise ValueError(f"release event schema violation ({name}): {problems}")
    return event
