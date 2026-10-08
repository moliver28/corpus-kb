"""Cycle checkpoint sealing + halt/approval rendering (todo 20).

``_seal`` writes each cycle checkpoint AND projects it before returning:
resume reads ``research_runs.checkpoint`` (the analytics projector's row),
so a checkpoint that only lived in the event store would leave the next
invocation stale. Halts print guide_copy's taught block (guide mode) and
emit the schema-pinned events; every prose string is a guide_copy constant.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.research import guide_copy
from corpus_kb.research.cycle_events import (
    EVENT_APPROVAL_DENIED,
    EVENT_AWAITING_APPROVAL,
    EVENT_CYCLE_HALTED,
    EVENT_GATE_RAISED,
    CycleRecorder,
)
from corpus_kb.research.cycle_gates import (
    EXIT_APPROVAL_DENIED,
    EXIT_AWAITING_APPROVAL,
    GATE_CODEBOOK_PROMOTION,
    GATE_DRIFT_ALARM,
    GATE_EXIT_CODES,
    GATE_GRAY_ZONE_ESCALATION,
    GATE_HUMAN_PARITY_BREACH,
    GATE_INTERPRETIVE_CODE_REVIEW,
    GATE_OVERLAP_CONFLICT,
    GATE_THRESHOLD_UNRELIABLE,
    GateFinding,
)
from corpus_kb.research.cycle_stages import _catch_up_all as _catch_up_stacks

GATE_GUIDE: dict[str, tuple[str, str, str]] = {
    GATE_CODEBOOK_PROMOTION: (
        guide_copy.GATE_CODEBOOK_PROMOTION_TAUGHT,
        guide_copy.GATE_CODEBOOK_PROMOTION_LOOK,
        guide_copy.GATE_CODEBOOK_PROMOTION_ACTION,
    ),
    GATE_INTERPRETIVE_CODE_REVIEW: (
        guide_copy.GATE_INTERPRETIVE_TAUGHT,
        guide_copy.GATE_INTERPRETIVE_LOOK,
        guide_copy.GATE_INTERPRETIVE_ACTION,
    ),
    GATE_GRAY_ZONE_ESCALATION: (
        guide_copy.GATE_GRAY_ZONE_TAUGHT,
        guide_copy.GATE_GRAY_ZONE_LOOK,
        guide_copy.GATE_GRAY_ZONE_ACTION,
    ),
    GATE_DRIFT_ALARM: (
        guide_copy.GATE_DRIFT_TAUGHT,
        guide_copy.GATE_DRIFT_LOOK,
        guide_copy.GATE_DRIFT_ACTION,
    ),
    GATE_OVERLAP_CONFLICT: (
        guide_copy.GATE_OVERLAP_TAUGHT,
        guide_copy.GATE_OVERLAP_LOOK,
        guide_copy.GATE_OVERLAP_ACTION,
    ),
    GATE_THRESHOLD_UNRELIABLE: (
        guide_copy.GATE_THRESHOLD_TAUGHT,
        guide_copy.GATE_THRESHOLD_LOOK,
        guide_copy.GATE_THRESHOLD_ACTION,
    ),
    GATE_HUMAN_PARITY_BREACH: (
        guide_copy.GATE_PARITY_TAUGHT,
        guide_copy.GATE_PARITY_LOOK,
        guide_copy.GATE_PARITY_ACTION,
    ),
}


async def catch_up_all(stack: tuple[Any, ...], tenant_id: UUID) -> None:
    await _catch_up_stacks(stack[0], stack[3], stack[4], stack[5], tenant_id)


async def seal(
    handler: Any,
    stack: tuple[Any, ...],
    tenant_id: UUID,
    run_id: UUID,
    payload: dict[str, object],
) -> None:
    """Write a cycle checkpoint AND project it before returning.

    A checkpoint that only lived in the event store would leave the next
    invocation stale (it would re-run covered stages, and a gate_halt would
    never suppress).
    """
    handler.checkpoint_coding_run(tenant_id, run_id, payload)
    await catch_up_all(stack, tenant_id)


def render_taught_block(finding: GateFinding, proposal_context: list[str]) -> None:
    taught, look, _action = GATE_GUIDE[finding.gate]
    print()
    print(taught)
    print(look)
    if proposal_context:
        print()
        print(guide_copy.CYCLE_PROPOSALS_HEADER)
        for line in proposal_context:
            print(line)
    if finding.gate == "codebook_promotion":
        print()
        print(guide_copy.GATE_CODEBOOK_PROMOTION_PROMOTE_CONSEQUENCE)
        print(guide_copy.GATE_CODEBOOK_PROMOTION_SKIP_CONSEQUENCE)


def inductive_run_id(findings: list[GateFinding]) -> UUID:
    for finding in findings:
        run_id = finding.detail.get("inductive_run_id")
        if run_id:
            return UUID(str(run_id))
    raise ValueError("promotion halt without an inductive run id")


async def halt(
    handler: Any,
    stack: tuple[Any, ...],
    recorder: CycleRecorder,
    guide_mode: bool,
    pool: asyncpg.Pool,
    tenant_id: UUID,
    run_id: UUID,
    stage: str,
    findings: list[GateFinding],
    halt_finding: GateFinding,
) -> int:
    exit_code = GATE_EXIT_CODES[halt_finding.gate]
    action = GATE_GUIDE[halt_finding.gate][2]
    await seal(
        handler,
        stack,
        tenant_id,
        run_id,
        {
            "kind": "gate_halt",
            "gate": halt_finding.gate,
            "stage": stage,
            "findings": [{"reason": f.reason, "gate": f.gate} for f in findings],
        },
    )
    for finding in findings:
        if finding.gate in GATE_EXIT_CODES:
            recorder.emit(
                EVENT_GATE_RAISED,
                run_id=str(run_id),
                gate=finding.gate,
                reason=finding.reason,
            )
    if guide_mode:
        context = (
            await proposal_lines(pool, tenant_id, inductive_run_id(findings))
            if halt_finding.gate == "codebook_promotion"
            else []
        )
        render_taught_block(halt_finding, context)
    recorder.emit(
        EVENT_CYCLE_HALTED,
        run_id=str(run_id),
        gate=halt_finding.gate,
        exit_code=exit_code,
        action=action,
    )
    handler.stop_coding_run(tenant_id, run_id)
    return exit_code


async def approval(
    recorder: CycleRecorder,
    handler: Any,
    stack: tuple[Any, ...],
    tenant_id: UUID,
    run_id: UUID,
    stage: str,
    approve: Callable[[str], Awaitable[bool]] | None,
) -> int | None:
    """One on-mode approval boundary; returns a stop exit code or None."""
    if approve is None:
        recorder.emit(EVENT_AWAITING_APPROVAL, run_id=str(run_id), stage=stage)
        handler.stop_coding_run(tenant_id, run_id)
        return EXIT_AWAITING_APPROVAL
    if await approve(stage):
        return None
    recorder.emit(EVENT_APPROVAL_DENIED, run_id=str(run_id), stage=stage)
    await seal(handler, stack, tenant_id, run_id, {"kind": "approval_denied", "stage": stage})
    handler.stop_coding_run(tenant_id, run_id)
    return EXIT_APPROVAL_DENIED


async def proposal_lines(
    pool: asyncpg.Pool, tenant_id: UUID, run_id: UUID, per_proposal: int = 3
) -> list[str]:
    """Taught-block decision context: pending proposals + example units."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    lines: list[str] = []
    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT proposed_id, suggested_label, n_members, member_unit_ids "
            "FROM research_proposed_codes WHERE tenant_id = $1 AND run_id = $2 "
            "ORDER BY proposed_id",
            tenant_id,
            run_id,
        )
        for row in rows:
            lines.append(
                guide_copy.CYCLE_PROPOSAL_LINE.format(
                    status="pending",
                    proposed_id=int(row["proposed_id"]),
                    label=str(row["suggested_label"]),
                    n_members=int(row["n_members"] or 0),
                )
            )
            ids = [int(x) for x in (row["member_unit_ids"] or [])][:per_proposal]
            if ids:
                texts = await conn.fetch(
                    "SELECT text FROM research_units "
                    "WHERE tenant_id = $1 AND unit_id = ANY($2::bigint[]) ORDER BY unit_id",
                    tenant_id,
                    ids,
                )
                lines.append(guide_copy.CYCLE_PROPOSAL_EXAMPLES_HEADER)
                lines.extend(
                    guide_copy.CYCLE_PROPOSAL_EXAMPLE_LINE.format(text=str(t["text"])[:140])
                    for t in texts
                )
    return lines
