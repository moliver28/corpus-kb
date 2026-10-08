"""Coding aggregates (todo-11 (a)): CodingRun coordinator + CodingAssignment.

CodingRun is a THIN coordinator only (Started / CheckpointComputed with
ISR+coverage payloads / Stopped) — it holds no per-unit state.

CodingAssignment is PER-UNIT (one aggregate instance per unit+code+run
decision), so concurrent batch dispatch never serializes on one aggregate
version (preserves the bundle fix 6adad8a's split-dispatch behavior).

TENANT CONTRACT (r8): every event signature carries tenant_id explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from eventsourcing.domain import Aggregate, event

MAX_ASSIGNMENTS_PER_EVENT = 100
MAX_SIGNALS_PER_EVENT = 100


@dataclass
class CodingRun(Aggregate):
    """Thin per-run coordinator; ISR/coverage numbers land in checkpoints."""

    tenant_id: UUID
    llm_name: str = ""
    llm_version: str = ""
    embed_model: str = ""
    model_revision: str = ""
    params: dict[str, object] = field(default_factory=dict)
    state: str = "started"
    checkpoints: list[dict[str, object]] = field(default_factory=list)
    stopped_at: str | None = None

    @event("Started")
    def __init__(
        self,
        tenant_id: UUID,
        llm_name: str = "",
        llm_version: str = "",
        embed_model: str = "",
        model_revision: str = "",
        params: dict[str, object] | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.llm_name = llm_name
        self.llm_version = llm_version
        self.embed_model = embed_model
        self.model_revision = model_revision
        self.params = params or {}
        self.state = "started"
        self.checkpoints = []
        self.stopped_at = None

    @event("CheckpointComputed")
    def add_checkpoint(self, tenant_id: UUID, payload: dict[str, object]) -> None:
        """Record one checkpoint (ISR, coverage, gray-zone share, residuals)."""
        payload = {**payload, "tenant_id": str(tenant_id)}
        self.checkpoints.append(payload)
        self.state = "checkpoint"

    @event("Stopped")
    def stop(self, tenant_id: UUID, stopped_at: str) -> None:
        """Close the run (idempotent final state)."""
        self.tenant_id = tenant_id
        self.state = "stopped"
        self.stopped_at = stopped_at


@dataclass
class CodingAssignment(Aggregate):
    """Per-unit coding decision (event-sourced; review decisions appended)."""

    tenant_id: UUID
    unit_id: int
    code_id: UUID
    run_id: UUID
    cb_version_id: UUID | None = None
    sim_answer: float | None = None
    sim_qa: float | None = None
    sim_q: float | None = None
    evidence_basis: str | None = None
    stance: str | None = None
    term_origin: str | None = None
    rationale: str = ""
    confidence: str | None = None
    tier_fired: int | None = None
    status: str = "auto"
    reviewed: bool = False

    @event("Recorded")
    def __init__(
        self,
        tenant_id: UUID,
        unit_id: int,
        code_id: UUID,
        run_id: UUID,
        cb_version_id: UUID | None = None,
        sim_answer: float | None = None,
        sim_qa: float | None = None,
        sim_q: float | None = None,
        evidence_basis: str | None = None,
        stance: str | None = None,
        term_origin: str | None = None,
        rationale: str = "",
        confidence: str | None = None,
        tier_fired: int | None = None,
        status: str = "auto",
    ) -> None:
        self.tenant_id = tenant_id
        self.unit_id = unit_id
        self.code_id = code_id
        self.run_id = run_id
        self.cb_version_id = cb_version_id
        self.sim_answer = sim_answer
        self.sim_qa = sim_qa
        self.sim_q = sim_q
        self.evidence_basis = evidence_basis
        self.stance = stance
        self.term_origin = term_origin
        self.rationale = rationale
        self.confidence = confidence
        self.tier_fired = tier_fired
        # Decision routing ("auto" | "review") rides the event so the review
        # queue (research_assignments.status='review') is event-derivable.
        self.status = status
        self.reviewed = False

    @event("Reviewed")
    def review(self, tenant_id: UUID, reviewer: str, decision: str, note: str = "") -> None:
        """Human review: accept ('confirmed') or override."""
        self.tenant_id = tenant_id
        self.reviewed = True
        self.status = "confirmed" if decision == "accept" else "overridden"

    @event("SignalRecorded")
    def record_signals(
        self,
        tenant_id: UUID,
        unit_id: int,
        run_id: UUID,
        signals: list[dict[str, object]],
    ) -> None:
        """Attach uncertainty signals (tier, entropies, flags) for this unit.

        unit_id/run_id ride the event signature so the projector can satisfy
        research_signals' FK without reading aggregate state it does not have.
        """
        if not 0 < len(signals) <= MAX_SIGNALS_PER_EVENT:
            raise ValueError(f"signal batch must be 1..{MAX_SIGNALS_PER_EVENT}")
        for signal in signals:
            signal["tenant_id"] = str(tenant_id)
        self.signal_count = getattr(self, "signal_count", 0) + len(signals)
