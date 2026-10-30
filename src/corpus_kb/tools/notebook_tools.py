"""Notebook MCP-tool functions (todo 18, v5 §15).

House pattern (tools/research_tools.py): async functions over an asyncpg
pool that MCP wrappers call. The CLI (corpus-kb research ask|evidence|
uncoded|overlap) shares these. The generation leg uses LlmHandler; the
retrieval-only leg performs ZERO LLM calls.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from corpus_kb.contracts.model import DEFAULT_TENANT_ID as DEFAULT_TENANT
from corpus_kb.research.notebook import NotebookQuery, notebook_ask
from corpus_kb.research.notebook_views import (
    evidence_for_code,
    overlap_view,
    uncoded_units,
)

__all__ = [
    "notebook_ask_tool",
    "notebook_evidence_tool",
    "notebook_overlap_tool",
    "notebook_uncoded_tool",
]


def _uuid(value: str) -> UUID:
    return UUID(value)


def _config() -> dict[str, object]:
    from corpus_kb.config import load_config

    return load_config()


async def notebook_ask_tool(
    question: str,
    pg_pool: asyncpg.Pool,
    project_id: str,
    tenant_id: str = DEFAULT_TENANT,
    k: int = 6,
    retrieval_only: bool = False,
    source_type: str | None = None,
    speaker_role: str | None = None,
    topic_id: int | None = None,
    doc_ids: list[str] | None = None,
    code: str | None = None,
    min_confidence: str | None = None,
    review_status: str | None = None,
) -> dict[str, object]:
    """Grounded notebook Q&A with per-sentence citations.

    The tool answers one question for one project. It ranks evidence
    with the parent-child retrieval stack, then asks the LLM to answer
    from that evidence only. Every answer sentence carries [n] citations
    into the evidence list; the tool drops sentences without a valid
    index. With retrieval_only=True the tool skips the LLM leg and
    returns the ranked evidence (zero LLM calls).

    Filters narrow the evidence pool. They combine with AND:
        question: The question text. Required.
        pg_pool: The asyncpg pool. Injected by the MCP wrapper.
        project_id: Project UUID. Required.
        tenant_id: Tenant UUID. Defaults to the placeholder tenant.
        k: Number of evidence excerpts. Default 6.
        retrieval_only: True returns evidence only, with no LLM call.
        source_type: Keep this document source type only.
        speaker_role: Keep this speaker role only.
        topic_id: Keep this topic id only.
        doc_ids: Keep these document UUIDs only.
        code: Keep units coded with this code name or UUID only.
        min_confidence: Confidence floor. One of high, medium, low.
            high keeps high-confidence units. medium adds medium units.
            low keeps all units.
        review_status: Review state. One of auto, review, confirmed,
            overridden.

    Returns:
        A dict with "status" and "llm_calls". status is one of:
        "answer": "sentences" (text + citations per sentence) and
            "evidence".
        "evidence": "evidence" only, plus "note" when the LLM leg could
            not run or produced no citable sentence.
        "empty": "message"; no evidence matched the filters.
        "error": "message"; a filter value was not valid.
    """
    from corpus_kb.handlers.llm_handler import LlmHandler
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.rag import create_embedder

    cfg = _config()
    embedder = ResearchEmbedder(pg_pool, create_embedder(cfg, pg_pool))
    llm = None if retrieval_only else LlmHandler(cfg)
    return await notebook_ask(
        pg_pool,
        embedder,
        llm,
        NotebookQuery(
            question=question,
            tenant_id=_uuid(tenant_id),
            project_id=_uuid(project_id),
            k=int(k),
            retrieval_only=retrieval_only,
            source_type=source_type,
            speaker_role=speaker_role,
            topic_id=int(topic_id) if topic_id is not None else None,
            doc_ids=tuple(_uuid(d) for d in (doc_ids or ())),
            code=code,
            min_confidence=min_confidence,
            review_status=review_status,
        ),
    )


async def notebook_evidence_tool(
    code: str,
    pg_pool: asyncpg.Pool,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
    version_id: str | None = None,
    limit: int = 200,
) -> dict[str, object]:
    """Evidence for one code: ranked units with flags + conformal set sizes."""
    return await evidence_for_code(
        pg_pool,
        _uuid(tenant_id),
        code,
        project_id=_uuid(project_id) if project_id else None,
        version_id=_uuid(version_id) if version_id else None,
        limit=int(limit),
    )


async def notebook_uncoded_tool(
    pg_pool: asyncpg.Pool,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
    version_id: str | None = None,
    k: int = 25,
) -> dict[str, object]:
    """Uncoded units ranked by nearest-code margin (missing-code radar)."""
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.rag import create_embedder

    cfg = _config()
    embedder = ResearchEmbedder(pg_pool, create_embedder(cfg, pg_pool))
    return await uncoded_units(
        pg_pool,
        embedder,
        _uuid(tenant_id),
        project_id=_uuid(project_id) if project_id else None,
        version_id=_uuid(version_id) if version_id else None,
        k=int(k),
    )


async def notebook_overlap_tool(
    pg_pool: asyncpg.Pool,
    project_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
    version_id: str | None = None,
    delta_amb: float = 0.05,
) -> dict[str, object]:
    """Code-overlap view: flagged pairs, confusion, borderline units."""
    return await overlap_view(
        pg_pool,
        _uuid(tenant_id),
        project_id=_uuid(project_id) if project_id else None,
        version_id=_uuid(version_id) if version_id else None,
        delta_amb=float(delta_amb),
    )
