"""Reviewer-override execution surface: `corpus-kb review accept|override`.

Reviewer overrides (including link corrections) feed the weight refit and
the G3 audit (r7/r10), so executing them is a NAMED surface: every human
decision goes through ResearchHandler.review_assignment (a CodingAssignment
event) and lands as a research_reviews row via the assignment projector,
which also flips the assignment status. The CLI command in cli.py delegates
here; tests drive this module directly.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.handlers.research_handler import ResearchHandler

DECISIONS = ("accept", "override")


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
    handler = ResearchHandler(pool)
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
    from corpus_kb.domain.application import get_app
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._common import DEFAULT_TENANT
    from corpus_kb.projections.research.assignment_projector import AssignmentProjector
    from corpus_kb.projections.research_projection import RESEARCH_PROJECTION_NAME

    app = get_app(conn_str)
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
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
