"""Assignment projector — CodingAssignment events into research_assignments
+ research_reviews (todo-11 (c)).

Routing defaults (v5 §8, G5): question-dependent evidence routes to review BY
DEFAULT (status='review', confidence capped at medium); interpretive codes
and deny/deflect stances likewise never auto-accept. Review decisions are
appended as research_reviews rows and flip the assignment status.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from corpus_kb.projections.research._common import event_payload, require_tenant
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)


class AssignmentProjector:
    """Projects per-unit CodingAssignment aggregate events."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def on_recorded(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        status = self._initial_status(payload)
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO research_assignments
                (tenant_id, assignment_aggregate_id, unit_id, code_id, cb_version_id,
                 run_id, sim_answer, sim_qa, sim_q, evidence_basis, stance,
                 term_origin, rationale, confidence, tier_fired, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                ON CONFLICT (tenant_id, assignment_aggregate_id) DO UPDATE SET
                    sim_answer = EXCLUDED.sim_answer,
                    sim_qa = EXCLUDED.sim_qa,
                    sim_q = EXCLUDED.sim_q,
                    evidence_basis = EXCLUDED.evidence_basis,
                    stance = EXCLUDED.stance,
                    term_origin = EXCLUDED.term_origin,
                    rationale = EXCLUDED.rationale,
                    confidence = EXCLUDED.confidence,
                    tier_fired = EXCLUDED.tier_fired,
                    status = EXCLUDED.status
                """,
                str(tenant_id),
                str(payload["aggregate_id"]),
                int(payload.get("unit_id", 0)),
                str(payload.get("code_id", "")),
                _opt_str(payload.get("cb_version_id")),
                _opt_str(payload.get("run_id")),
                payload.get("sim_answer"),
                payload.get("sim_qa"),
                payload.get("sim_q"),
                _opt_str(payload.get("evidence_basis")),
                _opt_str(payload.get("stance")),
                _opt_str(payload.get("term_origin")),
                str(payload.get("rationale", "")),
                self._capped_confidence(payload),
                payload.get("tier_fired"),
                status,
            )

    async def on_reviewed(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        aggregate_id = str(payload["aggregate_id"])
        decision = "confirmed" if payload.get("decision") == "accept" else "overridden"
        async with tenant_connection(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT assignment_id FROM research_assignments
                WHERE tenant_id = $1 AND assignment_aggregate_id = $2
                """,
                str(tenant_id),
                aggregate_id,
            )
            if row is None:
                raise ValueError(f"review for unknown assignment aggregate {aggregate_id}")
            await conn.execute(
                """
                INSERT INTO research_reviews
                (tenant_id, assignment_id, reviewer, decision, note)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (tenant_id, assignment_id, reviewer) DO UPDATE SET
                    decision = EXCLUDED.decision, note = EXCLUDED.note
                """,
                str(tenant_id),
                int(row["assignment_id"]),
                str(payload.get("reviewer", "")),
                decision,
                str(payload.get("note", "")),
            )
            await conn.execute(
                """
                UPDATE research_assignments SET status = $3
                WHERE tenant_id = $1 AND assignment_aggregate_id = $2
                """,
                str(tenant_id),
                aggregate_id,
                decision,
            )

    def _initial_status(self, payload: dict[str, Any]) -> str:
        """Decision routing: prefer the event's explicit status (deductive v2
        routing rides the Recorded event); fall back to evidence_basis/stance
        derivation for legacy events recorded before the status field."""
        status = payload.get("status")
        if status in ("auto", "review"):
            return str(status)
        evidence_basis = payload.get("evidence_basis")
        if evidence_basis == "question_dependent":
            return "review"
        stance = payload.get("stance")
        if stance in ("deny", "deflect"):
            return "review"
        return "auto"

    def _capped_confidence(self, payload: dict[str, Any]) -> str | None:
        confidence = payload.get("confidence")
        if payload.get("evidence_basis") == "question_dependent" and confidence == "high":
            return "medium"
        return _opt_str(confidence)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)
