"""Cycle stage runners + gate data reads (todo 20).

Every stage delegates to the EXISTING named surface (dynamic ingest,
run_inductive, run_deductive, run_keyword_synthesis, build_report,
notebook_ask) with the demo's production wiring - the cycle cannot drift
from the step commands because it calls the same functions. Gate reads
are plain read-model SELECTs; there is NO new analytics here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from corpus_kb.research import guide_copy


# gate -> (taught copy, what-to-look-at, actionable next command)
async def _catch_up_all(
    reader: Any, projection: Any, docs: Any, embeds: Any, tenant_id: UUID
) -> None:
    from corpus_kb.research.demo import _catch_up

    await _catch_up(reader, projection, docs, embeds, tenant_id)


def build_stack(pool: Any, cfg: dict[str, Any], conn_str: str) -> tuple[Any, ...]:
    """The demo's production wiring: (reader, handler, embedder, proj, docs, embeds)."""
    from corpus_kb.research.demo import _wiring

    return _wiring(pool, cfg, conn_str)


async def stage_ingest(
    pool: Any,
    stack: tuple[Any, ...],
    tenant_id: UUID,
    project_id: UUID | None,
    ingest_dir: str | None,
) -> dict[str, object]:
    reader, handler, _embedder, projection, docs, embeds = stack
    if not ingest_dir:
        return {"status": "success", "ingested": 0, "note": "no drop directory given"}
    from corpus_kb.research.dynamic_ingest import watch_once

    async def ingest_one(path: Path) -> None:
        await handler.ingest_transcript(tenant_id, str(path), project_id)
        await _catch_up_all(reader, projection, docs, embeds, tenant_id)

    ingested = await watch_once(pool, tenant_id, Path(ingest_dir), ingest_one)
    return {"status": "success", "ingested": ingested}


async def stage_inductive(
    pool: Any, stack: tuple[Any, ...], tenant_id: UUID, cfg: dict[str, Any], project_id: UUID | None
) -> dict[str, object]:
    from corpus_kb.handlers.llm_handler import LlmHandler
    from corpus_kb.research.inductive_run import run_inductive

    reader, _handler, embedder, projection, docs, embeds = stack
    summary = await run_inductive(
        pool,
        tenant_id,
        project_id=project_id,
        llm=LlmHandler(cfg),
        embedder=embedder,
        cfg=cfg,
    )
    await _catch_up_all(reader, projection, docs, embeds, tenant_id)
    return summary


async def stage_deductive(
    pool: Any,
    stack: tuple[Any, ...],
    tenant_id: UUID,
    project_id: UUID | None,
    version_id: UUID | None,
) -> dict[str, object]:
    from corpus_kb.research.cycle_reads import latest_version_id
    from corpus_kb.research.deductive_run import run_deductive

    resolved = version_id or await latest_version_id(pool, tenant_id)
    if resolved is None:
        return {"status": "unavailable", "reason": "no codebook version exists yet"}
    reader, _handler, _embedder, projection, docs, embeds = stack
    summary = await run_deductive(pool, tenant_id, resolved, project_id=project_id)
    await _catch_up_all(reader, projection, docs, embeds, tenant_id)
    return summary


async def stage_keywords(pool: Any, tenant_id: UUID, version_id: UUID | None) -> dict[str, object]:
    from corpus_kb.research.cycle_reads import latest_version_id
    from corpus_kb.research.keyword_surface import run_keyword_synthesis

    resolved = version_id or await latest_version_id(pool, tenant_id)
    if resolved is None:
        return {"status": "unavailable", "reason": "no codebook version exists yet"}
    return await run_keyword_synthesis(pool, tenant_id, resolved)


async def stage_report(
    pool: Any,
    stack: tuple[Any, ...],
    tenant_id: UUID,
    project_id: UUID | None,
    version_id: UUID | None,
) -> dict[str, object]:
    from corpus_kb.research.cycle_reads import latest_version_id
    from corpus_kb.research.governance_report import to_dict, validate_required
    from corpus_kb.research.report_runner import build_report

    resolved = version_id or await latest_version_id(pool, tenant_id)
    if resolved is None:
        return {"status": "unavailable", "reason": "no codebook version exists yet"}
    report = await build_report(
        pool,
        tenant_id,
        resolved,
        level=guide_copy.LEVEL_NOVICE,
        run_manifest={"project_id": str(project_id) if project_id else None},
    )
    payload = to_dict(report)
    missing = validate_required(payload)
    if missing:
        return {"status": "unavailable", "reason": f"report missing required fields: {missing}"}
    return payload


async def stage_notebook(
    pool: Any,
    stack: tuple[Any, ...],
    tenant_id: UUID,
    cfg: dict[str, Any],
    project_id: UUID | None,
    question: str | None,
) -> dict[str, object]:
    if not question:
        return {"status": "skipped", "reason": "no --question given"}
    if project_id is None:
        return {"status": "skipped", "reason": "no --project-id given"}
    from corpus_kb.handlers.llm_handler import LlmHandler
    from corpus_kb.research.notebook import NotebookQuery, notebook_ask

    reader, _handler, embedder, projection, docs, embeds = stack
    result = await notebook_ask(
        pool,
        embedder,
        LlmHandler(cfg),
        NotebookQuery(question=question, tenant_id=tenant_id, project_id=project_id),
    )
    await _catch_up_all(reader, projection, docs, embeds, tenant_id)
    # RAW result: the human surface renders the cited answer from it; the
    # stage_completed event bounds it via bounded_receipt.
    return dict(result)
