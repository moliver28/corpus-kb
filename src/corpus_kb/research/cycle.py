"""Research cycle orchestration (todo 20) — in/on/out-of-loop postures.

PURE ORCHESTRATION over the named surfaces (r7 Momus M-1): the chain is
ingest -> inductive -> deductive -> keywords -> report -> notebook, and
every stage calls the same functions the step commands call.

State is EVENT-SOURCED (no orchestration tables): the cycle IS a
CodingRun aggregate with ``params.kind='cycle'``; every stage boundary
lands a CheckpointComputed (``stage_complete`` / ``gate_halt`` /
``approval_denied`` / ``cycle_complete``), and resume = a NEW cycle run
that starts AFTER the last checkpoint's stage. A ``gate_halt`` on
``codebook_promotion`` is suppressed once: the human re-arms the cycle
by re-running it after acting on the gate (promote, or consciously
skip), so a resumed run never re-halts on the SAME gate at the SAME
checkpoint.

Modes: ``in`` executes exactly ONE stage; ``on`` executes one stage then
waits for explicit approval; ``out`` runs unattended except at the
configured halt gates. ``codebook_promotion`` is HARD-FLOORED
(cycle_gates): config may add gates, never remove the floor.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.research import guide_copy
from corpus_kb.research.cycle_events import (
    EVENT_CYCLE_COMPLETED,
    EVENT_CYCLE_STARTED,
    EVENT_STAGE_COMPLETED,
    EVENT_STAGE_STARTED,
    EVENT_STAGE_UNAVAILABLE,
    CycleRecorder,
    bounded_receipt,
)
from corpus_kb.research.cycle_gates import (
    EXIT_UNAVAILABLE,
    GateFinding,
    effective_halt_on,
)
from corpus_kb.research.cycle_reads import gate_findings_for
from corpus_kb.research.cycle_render import approval, seal
from corpus_kb.research.cycle_render import halt as halt_and_record
from corpus_kb.research.cycle_stages import (
    build_stack,
    stage_deductive,
    stage_inductive,
    stage_ingest,
    stage_keywords,
    stage_notebook,
    stage_report,
)
from corpus_kb.research.cycle_state import _last_cycle_run, resume_point
from corpus_kb.research.monitoring import Alarm, alarms_from_findings

STAGES: tuple[str, ...] = ("ingest", "inductive", "deductive", "keywords", "report", "notebook")
MODES: frozenset[str] = frozenset({"in", "on", "out"})


def stages_after(stage: str | None) -> tuple[str, ...]:
    """Stages remaining when the last completed stage was ``stage``."""
    if stage not in STAGES:
        return STAGES
    return STAGES[STAGES.index(stage) + 1 :]


def _halted_gate(
    findings: list[GateFinding], halt_set: frozenset[str], suppressed: str | None
) -> GateFinding | None:
    for finding in findings:
        if finding.gate in halt_set and finding.gate != suppressed:
            return finding
    return None


def _configured_halt_on(cfg: dict[str, Any]) -> list[str]:
    from typing import cast

    research_cfg = cast(dict[str, Any], cfg.get("research") or {})
    cycle_cfg = cast(dict[str, Any], research_cfg.get("cycle") or {})
    return [str(g) for g in (cycle_cfg.get("halt_on") or [])]


async def _propose_alarms(
    pool: asyncpg.Pool,
    handler: Any,
    tenant_id: UUID,
    version_id: UUID | None,
    alarms: list[Alarm],
    json_output: bool,
) -> dict[str, object] | None:
    """U25: turn this cycle's monitored alarms into a change proposal.

    Resolves the version's ACTIVE release through the release read tables;
    without a released parent there is NOTHING to propose against and the
    receipt says so. The proposal is a successor DRAFT via
    ``ReleaseService.propose_change`` — the released codebook/manifest/hash
    are never mutated. Returns the run-report receipt (None when the cycle
    has no codebook version or no alarms).

    ADVERSARIAL REVIEW F4: proposal/aggregate drift or a malformed manifest
    must not raise out of a COMPLETED cycle (the stages are done; an
    exception here used to leak the open run with no checkpoint). A failure
    lands as an honest ``proposal_failed`` receipt (reason string) instead.
    """
    from corpus_kb.research.cycle_reads import latest_version_id
    from corpus_kb.research.deduction_guard import resolve_active_release
    from corpus_kb.research.monitoring import propose_from_alarms

    if not alarms:
        return None
    resolved = version_id or await latest_version_id(pool, tenant_id)
    if resolved is None:
        return None
    try:
        active = await resolve_active_release(pool, tenant_id, resolved)
        if active is None or not active.is_released:
            receipt: dict[str, object] = {
                "status": "no_active_release",
                "codebook_version_id": str(resolved),
                "alarms": [alarm.block() for alarm in alarms],
                "reason": "monitoring alarms need a released parent; no released "
                "release exists for this codebook version",
            }
        else:
            aggregate = handler.app.repository.get(resolved)
            record = aggregate.releases[str(active.release_id)]
            parent_project = record.get("project_id")
            receipt = propose_from_alarms(
                aggregate,
                parent_release_id=UUID(str(active.release_id)),
                parent_manifest=dict(record["manifest_json"]),
                alarms=alarms,
                version_id=resolved,
                sink=handler.app,
                proposed_at=datetime.now(UTC).isoformat(),
                project_id=UUID(str(parent_project)) if parent_project else None,
            )
    except Exception as exc:
        receipt = {
            "status": "proposal_failed",
            "codebook_version_id": str(resolved),
            "alarms": [alarm.block() for alarm in alarms],
            "reason": f"{type(exc).__name__}: {exc}",
        }
    if not json_output:
        if receipt.get("status") == "change_proposed":
            print(
                guide_copy.CYCLE_PROPOSAL_RECORDED.format(
                    successor=receipt.get("successor_release_id"),
                    parent=receipt.get("parent_release_id"),
                )
            )
        elif receipt.get("status") == "no_active_release":
            print(guide_copy.CYCLE_PROPOSAL_NO_PARENT.format(version=resolved))
        elif receipt.get("status") == "proposal_failed":
            print(guide_copy.CYCLE_PROPOSAL_FAILED.format(reason=receipt.get("reason")))
    return receipt


async def _embedder_abstains(pool: asyncpg.Pool, config: dict[str, Any]) -> bool:
    """The demo's fail-fast gate: refuse to limp through an abstaining embedder."""
    from typing import cast

    from corpus_kb._setup.doctor_research import embedder_check

    pgml_installed: bool | None = None
    embedding_cfg = cast(dict[str, Any], config.get("embedding") or {})
    if str(embedding_cfg.get("provider", "")) == "pgml":
        async with pool.acquire() as conn:
            present = await conn.fetchval(
                "SELECT count(*) FROM pg_extension WHERE extname = 'pgml'"
            )
        pgml_installed = bool(present)
    return embedder_check(config, pgml_installed).status != "ok"


async def _start_run(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    cfg: dict[str, Any],
    mode: str,
    *,
    json_output: bool,
    guide: bool,
) -> tuple[Any, Any, tuple[Any, ...], str, str | None, bool]:
    """Open the coding run: resume point, stack, recorder, banners.

    Returns (recorder, handler, stack, run_id, resume_after, suppressed).
    Emits EVENT_CYCLE_STARTED and the guide/resume banners (when not
    json_output), and catches projections up after a human pause.
    """
    recorder = CycleRecorder(json_output=json_output)
    last = await _last_cycle_run(pool, tenant_id)
    resume_after, suppressed = resume_point(last)
    resume_from = str(last["run_id"]) if last else None
    reader, handler, _embedder, _projection, _docs, _embeds = build_stack(
        pool, cfg, str((cfg.get("database") or {}).get("connection_string", ""))
    )
    stack = (reader, handler, _embedder, _projection, _docs, _embeds)
    started = handler.start_coding_run(
        tenant_id,
        params={
            "kind": "cycle",
            "mode": mode,
            "resume_from_run": resume_from,
        },
    )
    run_id = str(started["run_id"])
    recorder.emit(EVENT_CYCLE_STARTED, run_id=run_id, mode=mode, stages=list(STAGES))
    if guide and not json_output:
        print(guide_copy.CYCLE_TITLE)
        print(guide_copy.CYCLE_INTRO)
        print(f"docs: {guide_copy.CYCLE_DOC}")
    if resume_after and resume_from and not json_output:
        print(guide_copy.CYCLE_RESUME_HINT.format(stage=resume_after, run_id=resume_from))
    if resume_after:
        # The human acted between runs (promote, review) through surfaces
        # that advance projections; re-read the world before continuing so
        # stage reads (e.g. the latest codebook version) see their actions.
        from corpus_kb.research.cycle_render import catch_up_all

        await catch_up_all(stack, tenant_id)
    return recorder, handler, stack, run_id, resume_after, suppressed


def _print_stage_view(stage: str, receipt: dict[str, Any], json_output: bool) -> None:
    """Human-facing stage output for the report and notebook stages."""
    if json_output:
        return
    from corpus_kb.research.demo import _print_ask, _print_report_summary
    from corpus_kb.research.governance_report import _mapping

    if stage == "report":
        _print_report_summary(_mapping(receipt.get("novice_view")))
    if stage == "notebook" and receipt.get("status") != "skipped":
        _print_ask(receipt)


async def _abort_unavailable_stage(
    handler: Any,
    stack: tuple[Any, ...],
    recorder: CycleRecorder,
    tenant_id: UUID,
    run_id: str,
    stage: str,
    receipt: dict[str, Any],
) -> int:
    """Seal a stage that could not run and stop the coding run."""
    recorder.emit(
        EVENT_STAGE_UNAVAILABLE,
        run_id=run_id,
        stage=stage,
        reason=str(receipt.get("reason", "")),
    )
    await seal(
        handler,
        stack,
        tenant_id,
        UUID(run_id),
        {"kind": "stage_unavailable", "stage": stage},
    )
    handler.stop_coding_run(tenant_id, UUID(run_id))
    return EXIT_UNAVAILABLE


async def _post_stage_mode_stop(
    pool: asyncpg.Pool,
    handler: Any,
    stack: tuple[Any, ...],
    recorder: CycleRecorder,
    tenant_id: UUID,
    run_id: str,
    stage: str,
    alarms: list[Alarm],
    *,
    mode: str,
    codebook_version_id: UUID | None,
    json_output: bool,
    approve: Callable[[str], Awaitable[bool]] | None,
) -> int | None:
    """Mode-specific stop after a completed stage (exit code, or None to continue).

    ``in``: propose alarms and stop the run (a success needs no checkpoint —
    the proposal itself is event-sourced through the app; a failed proposal
    lands its receipt on the stage checkpoint, same kind/stage — resume
    semantics unchanged — instead of vanishing with the process (F4)).
    ``on``: ask the human between stages; stop when they say so.
    """
    if mode == "in":
        proposal = await _propose_alarms(
            pool, handler, tenant_id, codebook_version_id, alarms, json_output
        )
        if proposal is not None:
            await seal(
                handler,
                stack,
                tenant_id,
                UUID(run_id),
                {"kind": "stage_complete", "stage": stage, "change_proposals": proposal},
            )
        handler.stop_coding_run(tenant_id, UUID(run_id))
        return 0
    if mode == "on":
        return await approval(recorder, handler, stack, tenant_id, UUID(run_id), stage, approve)
    return None


async def run_cycle(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    mode: str = "out",
    project_id: UUID | None = None,
    ingest_dir: str | None = None,
    question: str | None = None,
    codebook_version_id: UUID | None = None,
    halt_on: list[str] | None = None,
    guide: bool = False,
    json_output: bool = False,
    approve: Callable[[str], Awaitable[bool]] | None = None,
    cfg: dict[str, Any] | None = None,
    allow_draft: bool = False,
) -> int:
    """Run the research cycle in one posture; returns the process exit code."""
    if mode not in MODES:
        raise ValueError(f"unknown cycle mode: {mode!r} (expected one of {sorted(MODES)})")
    from corpus_kb.config import load_config

    cfg = cfg if cfg is not None else load_config()
    config: dict[str, Any] = dict(cfg)
    if await _embedder_abstains(pool, config):
        if not json_output:
            print(guide_copy.CYCLE_NO_EMBEDDINGS)
        return EXIT_UNAVAILABLE
    configured = halt_on if halt_on is not None else _configured_halt_on(config)
    halt_set = effective_halt_on(configured)

    recorder, handler, stack, run_id, resume_after, suppressed = await _start_run(
        pool, tenant_id, config, mode, json_output=json_output, guide=guide
    )
    alarms: list[Alarm] = []  # U25: monitored alarms across this cycle's stages

    for stage in stages_after(resume_after):
        recorder.emit(EVENT_STAGE_STARTED, run_id=run_id, stage=stage)
        receipt = await _run_stage_or_consensus(
            stage,
            pool,
            stack,
            tenant_id,
            cfg,
            project_id,
            ingest_dir,
            question,
            codebook_version_id,
            allow_draft,
        )
        if str(receipt.get("status")) == "unavailable":
            return await _abort_unavailable_stage(
                handler, stack, recorder, tenant_id, run_id, stage, receipt
            )
        _print_stage_view(stage, receipt, json_output)
        recorder.emit(
            EVENT_STAGE_COMPLETED, run_id=run_id, stage=stage, receipt=bounded_receipt(receipt)
        )
        await seal(
            handler, stack, tenant_id, UUID(run_id), {"kind": "stage_complete", "stage": stage}
        )
        findings = await gate_findings_for(stage, receipt, pool, tenant_id, cfg)
        alarms.extend(alarms_from_findings(findings))
        halted = _halted_gate(findings, halt_set, suppressed)
        if halted is not None:
            return await halt_and_record(
                handler,
                stack,
                recorder,
                guide and not json_output,
                pool,
                tenant_id,
                UUID(run_id),
                stage,
                findings,
                halted,
            )
        stop = await _post_stage_mode_stop(
            pool,
            handler,
            stack,
            recorder,
            tenant_id,
            run_id,
            stage,
            alarms,
            mode=mode,
            codebook_version_id=codebook_version_id,
            json_output=json_output,
            approve=approve,
        )
        if stop is not None:
            return stop

    proposal_receipt = await _propose_alarms(
        pool, handler, tenant_id, codebook_version_id, alarms, json_output
    )
    seal_payload: dict[str, object] = {"kind": "cycle_complete", "stages": STAGES}
    if proposal_receipt is not None:
        # U25: the run-report payload surfaces the change proposals.
        seal_payload["change_proposals"] = proposal_receipt
    await seal(handler, stack, tenant_id, UUID(run_id), seal_payload)
    recorder.emit(EVENT_CYCLE_COMPLETED, run_id=run_id, stages=list(STAGES))
    handler.stop_coding_run(tenant_id, UUID(run_id))
    return 0


async def _run_stage(
    stage: str,
    pool: asyncpg.Pool,
    stack: tuple[Any, ...],
    tenant_id: UUID,
    cfg: dict[str, Any],
    project_id: UUID | None,
    ingest_dir: str | None,
    question: str | None,
    version_id: UUID | None,
    allow_draft: bool = False,
) -> dict[str, object]:
    if stage == "ingest":
        return await stage_ingest(pool, stack, tenant_id, project_id, ingest_dir)
    if stage == "inductive":
        return await stage_inductive(pool, stack, tenant_id, cfg, project_id)
    if stage == "deductive":
        # cfg/allow_draft reach the U24 guard inside run_deductive — the
        # injected config decides enforcement, never a disk reload (F8).
        return await stage_deductive(
            pool, stack, tenant_id, project_id, version_id, cfg=cfg, allow_draft=allow_draft
        )
    if stage == "keywords":
        return await stage_keywords(pool, tenant_id, version_id)
    if stage == "report":
        return await stage_report(pool, stack, tenant_id, project_id, version_id)
    return await stage_notebook(pool, stack, tenant_id, cfg, project_id, question)


async def _run_stage_or_consensus(
    stage: str,
    pool: asyncpg.Pool,
    stack: tuple[Any, ...],
    tenant_id: UUID,
    cfg: dict[str, Any],
    project_id: UUID | None,
    ingest_dir: str | None,
    question: str | None,
    version_id: UUID | None,
    allow_draft: bool = False,
) -> dict[str, object]:
    """The inductive stage becomes the U48 N-run consensus when configured.

    ``research.consensus.runs`` (live through cycle_consensus) decides: 1
    keeps the single-pass stage verbatim; N >= 2 runs N INDEPENDENT inductive
    passes and aggregates their proposals through the release-layer consensus
    before themes surface. Every other stage is unchanged.
    """
    if stage != "inductive":
        return await _run_stage(
            stage,
            pool,
            stack,
            tenant_id,
            cfg,
            project_id,
            ingest_dir,
            question,
            version_id,
            allow_draft,
        )
    from corpus_kb.research.cycle_consensus import consensus_settings, run_inductive_consensus

    if consensus_settings(cfg).runs <= 1:
        return await _run_stage(
            stage,
            pool,
            stack,
            tenant_id,
            cfg,
            project_id,
            ingest_dir,
            question,
            version_id,
            allow_draft,
        )

    async def run_once() -> dict[str, object]:
        return await _run_stage(
            stage,
            pool,
            stack,
            tenant_id,
            cfg,
            project_id,
            ingest_dir,
            question,
            version_id,
            allow_draft,
        )

    # runs > 1 here, so the consensus runner always returns a receipt; the
    # fallback keeps the stage contract (an honest unavailable, never None).
    receipt = await run_inductive_consensus(pool, tenant_id, cfg, project_id, run_once)
    if receipt is None:
        return {"status": "unavailable", "reason": "consensus runner returned no receipt"}
    return receipt
