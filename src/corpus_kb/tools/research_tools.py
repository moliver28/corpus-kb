"""Research MCP-tool functions (todo-11 (d), todo-14 coding run).

House pattern (tools/ingest_tools.py): async functions over an asyncpg pool
that MCP wrappers call. The CLI (corpus-kb research ... / coding run)
shares these.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

import asyncpg

from corpus_kb.handlers.research_handler import get_research_handler

__all__ = [
    "codebook_promote",
    "coding_run",
    "inductive_run",
    "research_ingest",
    "research_ingest_transcript",
    "research_report",
    "review_execute",
]

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


async def research_ingest_transcript(
    file_path: str,
    pg_pool: asyncpg.Pool,
    project_id: str | None = None,
    title: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
    force: bool = False,
) -> dict[str, object]:
    """Ingest ONE transcript file (turns -> roles -> exchanges -> events)."""
    handler = get_research_handler(pg_pool)
    return await handler.ingest_transcript(
        UUID(tenant_id),
        file_path,
        project_id=UUID(project_id) if project_id else None,
        title=title,
        force=force,
    )


async def research_ingest(
    path: str,
    pg_pool: asyncpg.Pool,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
    force: bool = False,
) -> dict[str, object]:
    """Dynamic ingestion: file, directory, or glob with ledger dedup."""
    handler = get_research_handler(pg_pool)
    return await handler.ingest_path(
        UUID(tenant_id),
        path,
        project_id=UUID(project_id) if project_id else None,
        force=force,
    )


def supported_suffixes() -> set[str]:
    from corpus_kb.research.dynamic_ingest import SUPPORTED_SUFFIXES

    return set(SUPPORTED_SUFFIXES)


async def coding_run(
    pool: asyncpg.Pool,
    codebook_version_id: str,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
    cal_alpha: float = 0.1,
) -> dict[str, object]:
    """Launch one deductive coding run (v5 §8) and advance projections.

    Scores codable units against rebuilt multi-prototype code vectors over
    the three views, applies calibrated tau/margin decisions with conformal
    routing, and records CodingRun/CodingAssignment events; the research
    projection catch-up lands the assignment rows.
    """
    from corpus_kb.config import load_config
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.rag import create_embedder
    from corpus_kb.research.deductive_run import run_deductive

    summary = await run_deductive(
        pool,
        UUID(tenant_id),
        UUID(codebook_version_id),
        project_id=UUID(project_id) if project_id else None,
        cal_alpha=cal_alpha,
    )
    app = get_research_handler(pool).app
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    cfg = load_config()
    embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    await projection.catch_up(reader)
    return summary


async def inductive_run(
    pool: asyncpg.Pool,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """Launch one inductive pass (v5 §9) and advance projections.

    Summarizes units to atomic observations (temp 0), embeds them, runs the
    UMAP+HDBSCAN pilot, grows incrementally with soft-entropy signals, and
    writes the proposed_code log + noise queue. Returns status "unavailable"
    (never raises) when the optional ``inductive`` extra is absent.
    """
    from corpus_kb.config import load_config
    from corpus_kb.handlers.llm_handler import LlmHandler
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.rag import create_embedder
    from corpus_kb.research.inductive_run import run_inductive

    cfg = load_config()
    embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
    summary = await run_inductive(
        pool,
        UUID(tenant_id),
        project_id=UUID(project_id) if project_id else None,
        llm=LlmHandler(cfg),
        embedder=embedder,
        cfg=cfg,
    )
    app = get_research_handler(pool).app
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    await projection.catch_up(reader)
    return summary


async def codebook_promote(
    pool: asyncpg.Pool,
    proposed_id: int,
    name: str,
    definition: str,
    inclusion: str = "",
    exclusion: str = "",
    label: str | None = None,
    tau_dup: float | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """Promote one proposed code into a NEW codebook version (human gate).

    Re-derives prototypes from member units in the deductive space (r7),
    runs the duplicate gate (tau_dup), then mints CodebookVersion
    Created + CodeAdded + PrototypeUpdated events; status "duplicate_blocked"
    with a merge suggestion when the gate fires.
    """
    from corpus_kb.config import load_config
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.rag import create_embedder
    from corpus_kb.research.inductive_run import inductive_config
    from corpus_kb.research.promote_code import promote_proposal

    cfg = load_config()
    effective_tau = tau_dup if tau_dup is not None else float(str(inductive_config(cfg)["tau_dup"]))
    result = await promote_proposal(
        pool,
        UUID(tenant_id),
        int(proposed_id),
        name,
        definition,
        inclusion=inclusion,
        exclusion=exclusion,
        tau_dup=effective_tau,
        label=label,
    )
    app = get_research_handler(pool).app
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    await projection.catch_up(reader)
    return result


async def research_report(
    pool: asyncpg.Pool,
    codebook_version_id: str | None = None,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
    level: str = "novice",
) -> dict[str, object]:
    """Governance report (v5 section 11/13/14) as ONE schema-pinned artifact.

    Exhaustiveness residuals + calibrated tau_res per source type, coverage
    curve, candidate missing codes (queued for review), bootstrap cluster
    stability, semantic overlap, dual IRR (alpha + AC1), keyword sections
    with hit_location populations, G3 audit block, conformal set sizes, and
    the run manifest.
    """
    from corpus_kb.research.governance_report import to_dict, validate_required
    from corpus_kb.research.report_runner import build_report

    report = await build_report(
        pool,
        UUID(tenant_id),
        UUID(codebook_version_id) if codebook_version_id else None,
        level=level,
        run_manifest={"project_id": project_id},
    )
    payload = to_dict(report)
    missing = validate_required(payload)
    if missing:
        return {"status": "error", "missing_required_fields": missing}
    return payload


async def review_execute(
    pool: asyncpg.Pool,
    assignment_id: str,
    decision: str,
    reviewer: str,
    note: str = "",
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """Record one review accept/override decision and advance projections.

    The named review surface (todo 16 CLI, todo 18 MCP): every human decision
    becomes a CodingAssignment.Reviewed event projected into research_reviews.
    """
    from corpus_kb.config import load_config
    from corpus_kb.research.review_surface import execute_review

    cfg = load_config()
    db = cast(dict[str, object], cfg.get("database") or {})
    conn_str = str(db.get("connection_string", ""))
    return await execute_review(
        pool,
        UUID(tenant_id),
        conn_str,
        UUID(assignment_id),
        reviewer,
        decision,
        note,
    )
