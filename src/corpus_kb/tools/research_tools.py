"""Research MCP-tool functions (todo-11 (d), todo-14 coding run).

House pattern (tools/ingest_tools.py): async functions over an asyncpg pool
that MCP wrappers call. The CLI (corpus-kb research ... / coding run)
shares these.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from corpus_kb.handlers.research_handler import get_research_handler

__all__ = ["coding_run", "research_ingest", "research_ingest_transcript"]

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
