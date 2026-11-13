"""Cross-run cycle state (todo 20): resume anchors + the foreground watch.

Resume = the newest ``research_runs`` row with ``params.kind='cycle'``;
its LAST projected checkpoint tells the next invocation which stage comes
next (and which gate to suppress). The stage chain lives here too, so the
resume logic can step back one stage without a cycle.py import cycle.

An approval-denied checkpoint holds the flow: the next run continues
after the denied stage (skip). An EXPLICIT re-run clears it (issue 306,
owner ruling 2a): the denied stage runs again and the approval boundary
asks for a decision again. The watch re-arms itself, so its re-runs stay
implicit and keep the skip.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.research import guide_copy
from corpus_kb.storage.gateway import SqlGateway


async def _last_cycle_run(
    pool: asyncpg.Pool | SqlGateway,
    tenant_id: UUID,
) -> dict[str, Any] | None:
    """The newest cycle run's identity + last checkpoint (resume anchor)."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT run_id, checkpoint FROM research_runs "
            "WHERE tenant_id = $1 AND (params->>'kind') = 'cycle' "
            "ORDER BY started_at DESC, run_id DESC LIMIT 1",
            tenant_id,
        )
    if row is None:
        return None
    checkpoint = row["checkpoint"]
    if isinstance(checkpoint, str):
        checkpoint = json.loads(checkpoint)
    return {
        "run_id": UUID(str(row["run_id"])),
        "checkpoint": checkpoint if isinstance(checkpoint, dict) else {},
    }


STAGES: tuple[str, ...] = ("ingest", "inductive", "deductive", "keywords", "report", "notebook")


def stages_after(stage: str | None) -> tuple[str, ...]:
    """Stages remaining when the last completed stage was ``stage``."""
    if stage not in STAGES:
        return STAGES
    return STAGES[STAGES.index(stage) + 1 :]


def _stage_before(stage: str) -> str | None:
    """The stage before ``stage`` (None when ``stage`` opens the chain)."""
    index = STAGES.index(stage)
    return STAGES[index - 1] if index > 0 else None


def resume_point(
    last: dict[str, Any] | None, *, retry_denied: bool = False
) -> tuple[str | None, str | None]:
    """(resume_after_stage, suppressed_gate) derived from the last checkpoint.

    An ``approval_denied`` checkpoint normally holds the flow: the next
    run continues AFTER the denied stage, so a re-run never retries it.
    With ``retry_denied`` (an explicit re-run, issue 306) the denial is
    cleared: the answer resumes AFTER the stage BEFORE the denied stage,
    so the chain starts AT it and the boundary asks again. The stage
    re-runs its work in the new run; its receipts stay event-sourced and
    honest. An unknown stage name falls back to the plain resume, the
    same as the other checkpoint kinds.
    """
    if not last:
        return None, None
    checkpoint = last["checkpoint"]
    kind = str(checkpoint.get("kind", ""))
    if kind in ("stage_complete", "gate_halt", "approval_denied"):
        stage = str(checkpoint.get("stage")) or None
        gate = str(checkpoint.get("gate")) if kind == "gate_halt" else None
        if kind == "approval_denied" and retry_denied and stage in STAGES:
            return _stage_before(stage), None
        return stage, (gate or None)
    return None, None


async def last_cycle_checkpoint(
    pool: asyncpg.Pool | SqlGateway,
    tenant_id: UUID,
) -> dict[str, object]:
    """The newest cycle run's final checkpoint (MCP receipt / diagnostics)."""
    last = await _last_cycle_run(pool, tenant_id)
    return dict(last["checkpoint"]) if last else {}


async def pending_files(pool: asyncpg.Pool | SqlGateway, tenant_id: UUID, directory: Path) -> int:
    from corpus_kb.research.dynamic_ingest import plan_file, scan_path

    pending = 0
    for path in await scan_path(directory):
        decision = await plan_file(pool, tenant_id, path)
        if not decision.skipped:
            pending += 1
    return pending


async def watch_cycle(
    pool: asyncpg.Pool | SqlGateway | SqlGateway,
    tenant_id: UUID,
    directory: str,
    *,
    interval_s: int = 10,
    max_arms: int | None = None,
    **cycle_kwargs: Any,
) -> int:
    """Foreground watch: re-arm the full cycle when new transcripts land.

    Idle = sleep(interval) between ledger polls (no busy-loop, no daemon).
    The cycle's own ingest stage consumes the pending files; the file-hash
    ledger makes the next idle poll see none (pickup-once semantics).

    Watch arms are automatic, never a human's explicit re-run: a denied
    stage keeps its denial (the skip stays) no matter what the caller
    passes in.
    """
    arms = 0
    cycle_kwargs.pop("retry_denied", None)
    json_output = bool(cycle_kwargs.get("json_output"))
    from corpus_kb.research.cycle import run_cycle

    while True:
        pending = await pending_files(pool, tenant_id, Path(directory))
        if pending:
            if not json_output:
                print(guide_copy.CYCLE_WATCH_ARMED.format(n=pending), flush=True)
            code = await run_cycle(
                pool,
                tenant_id,
                ingest_dir=directory,
                mode="out",
                retry_denied=False,
                **cycle_kwargs,
            )
            arms += 1
            if max_arms is not None and arms >= max_arms:
                return code
        elif not json_output:
            print(guide_copy.CYCLE_WATCH_IDLE.format(interval=interval_s), flush=True)
        await asyncio.sleep(interval_s)
