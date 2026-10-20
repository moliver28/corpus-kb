"""Reviewer-override execution surface: `corpus-kb review accept|override`.

Reviewer overrides (including link corrections) feed the weight refit and
the G3 audit (r7/r10), so executing them is a NAMED surface: every human
decision goes through ResearchHandler.review_assignment (a CodingAssignment
event) and lands as a research_reviews row via the assignment projector,
which also flips the assignment status. The CLI command in cli.py delegates
here; tests drive this module directly.

U48 EXTENSION: the binary DECISIONS are joined by the three-way reviewer
RUBRIC (``agreement | reasonable_alternative | not_reasonable`` — the
``good|partial|no match`` reading against a reference codebook) recorded as
``ReviewerRubricRecorded`` events on the CodebookVersion aggregate (the
wave-1 aggregate defines the event; the event store is its record — there
is deliberately no read table yet). ``aggregate_rubric`` folds recorded
verdicts into the run-report block. U37/U41/U47 routing + batching
composition lives in ``review_routing`` and is re-exported here so the
review surface is the single named entry point.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.handlers.research_handler import ResearchHandler
from corpus_kb.research.review_routing import (
    ROUTE_AUTO_ACCEPT,
    ROUTE_REVIEW,
    RoutingPlan,
    measurement_settings_from_config,
    next_review_batch,
    route_for_review,
)

__all__ = [
    "DECISIONS",
    "ROUTE_AUTO_ACCEPT",
    "ROUTE_REVIEW",
    "RUBRIC_AGREEMENT",
    "RUBRIC_NOT_REASONABLE",
    "RUBRIC_REASONABLE_ALTERNATIVE",
    "RUBRIC_VERDICTS",
    "RoutingPlan",
    "aggregate_rubric",
    "execute_review",
    "execute_rubric_review",
    "measurement_settings_from_config",
    "next_review_batch",
    "route_for_review",
    "validate_rubric_verdict",
]

DECISIONS = ("accept", "override")

RUBRIC_AGREEMENT = "agreement"
RUBRIC_REASONABLE_ALTERNATIVE = "reasonable_alternative"
RUBRIC_NOT_REASONABLE = "not_reasonable"
RUBRIC_VERDICTS = (
    RUBRIC_AGREEMENT,
    RUBRIC_REASONABLE_ALTERNATIVE,
    RUBRIC_NOT_REASONABLE,
)


async def execute_review(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    conn_str: str,
    assignment_aggregate_id: UUID,
    reviewer: str,
    decision: str,
    note: str = "",
) -> dict[str, Any]:
    """Record one reviewer decision and advance the projections.

    Args:
        pool: Application pool.
        tenant_id: Tenant scope (RLS).
        conn_str: Database URL (the events reader needs it for the app).
        assignment_aggregate_id: CodingAssignment aggregate id under review.
        reviewer: Reviewer identity recorded on the research_reviews row.
        decision: ``accept`` (confirm) or ``override`` (model was wrong).
        note: Optional free-text note (link corrections describe the fix).

    Returns:
        {"status": "success", "assignment_id", "decision", "projection": {...}}.
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    handler = ResearchHandler(pool, dsn=conn_str)
    result = handler.review_assignment(
        tenant_id,
        assignment_aggregate_id=assignment_aggregate_id,
        reviewer=reviewer,
        decision=decision,
        note=note,
    )
    projected = await _advance_assignments(pool, tenant_id, conn_str)
    return {
        "status": "success",
        "assignment_id": str(result.get("assignment_id", "")),
        "decision": decision,
        "projection": projected,
    }


def validate_rubric_verdict(verdict: str) -> str:
    """Fail loud on a verdict outside the three-way rubric (U48)."""
    if verdict not in RUBRIC_VERDICTS:
        raise ValueError(f"rubric verdict must be one of {RUBRIC_VERDICTS}, got {verdict!r}")
    return verdict


def aggregate_rubric(reviews: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Fold recorded rubric verdicts into the run-report block (U48).

    Honest aggregation: ``n == 0`` reports ``agreement_rate: None`` (unknown,
    never 0.0) and the per-verdict counts stay zero-filled so the report
    shape is stable.
    """
    counts = dict.fromkeys(RUBRIC_VERDICTS, 0)
    reviewers: set[str] = set()
    n = 0
    for review in reviews:
        verdict = str(review.get("verdict", ""))
        if verdict not in counts:
            continue
        counts[verdict] += 1
        n += 1
        reviewer = review.get("reviewer")
        if reviewer:
            reviewers.add(str(reviewer))
    return {
        "n": n,
        "counts": counts,
        "agreement_rate": (counts[RUBRIC_AGREEMENT] / n) if n else None,
        "n_reviewers": len(reviewers),
    }


async def execute_rubric_review(
    pool: asyncpg.Pool | None,
    tenant_id: UUID,
    conn_str: str,
    codebook_version_id: UUID,
    proposal_id: str,
    reviewer: str,
    verdict: str,
    note: str = "",
) -> dict[str, Any]:
    """Record one U48 rubric verdict on the codebook version aggregate.

    The verdict is an event on the aggregate (ReviewerRubricRecorded) — the
    event store is its record; no read table exists by design, so unlike
    :func:`execute_review` no projection advance (and no pool) is needed.
    The full aggregation over the version's rubric reviews comes back so the
    caller can surface it immediately.

    Returns:
        {"status": "success", "proposal_id", "verdict", "aggregation": {...}}.
    """
    validate_rubric_verdict(verdict)
    app = _app(conn_str)
    version = app.repository.get(codebook_version_id)
    version.record_rubric_review(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        reviewer=reviewer,
        verdict=verdict,
        note=note,
        recorded_at=_now_iso(),
    )
    app.save(version)
    return {
        "status": "success",
        "proposal_id": proposal_id,
        "verdict": verdict,
        "aggregation": aggregate_rubric(version.rubric_reviews.values()),
    }


def _app(conn_str: str) -> Any:
    """The eventsourcing Application (the aggregate save path)."""
    from corpus_kb.domain.application import get_app

    return get_app(conn_str)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


async def _advance_assignments(
    pool: asyncpg.Pool, tenant_id: UUID, conn_str: str
) -> dict[str, Any]:
    """Apply NEW CodingAssignment.Reviewed events through the projector.

    Reads since the research projection's current checkpoint WITHOUT
    advancing it: the review event was just appended at the tail, so only
    unprojected events are replayed (replaying from 0 would re-apply stale
    Reviewed events whose assignments were never projected — observed as a
    full-suite cross-test failure). A later full ResearchProjection.catch_up
    remains the checkpoint authority and re-applies idempotently.
    """
    from corpus_kb.domain.application import events_table_name, get_app
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._common import DEFAULT_TENANT
    from corpus_kb.projections.research.assignment_projector import AssignmentProjector
    from corpus_kb.projections.research_projection import RESEARCH_PROJECTION_NAME

    app = get_app(conn_str)
    reader = EventReader(pool, app.mapper, events_table_name(app))
    checkpoint = CheckpointManager(pool)
    cp = await checkpoint.get_checkpoint(RESEARCH_PROJECTION_NAME, DEFAULT_TENANT)
    last_sequence = int(cp["last_sequence"]) if cp and cp["last_sequence"] else 0
    projector = AssignmentProjector(pool)
    handled = 0
    while True:
        batch = await reader.read_since(last_sequence, limit=500)
        if not batch:
            break
        for notification in batch:
            last_sequence = notification.notification_id
            if notification.event_type != "CodingAssignment.Reviewed":
                continue
            handled += 1
            await projector.on_reviewed(notification)
        if len(batch) < 500:
            break
    return {"reviewed_events_applied": handled}
