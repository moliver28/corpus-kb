"""The eval runner: one golden file, two scored sections.

RETRIEVAL — every record's query runs the research search path, the same
machinery ``research/retrieval_eval.py`` scores: ``_unit_arms`` (dense +
FTS) fused by ``_rrf`` inside one tenant-scoped transaction. The ranked
list of chunk-id UUIDs is scored with the U45 metric functions
(recall@k, MRR, nDCG@10) — the eval numbers are the same species the
research benchmarks report, computed by the same code.

ANSWER — delegated to ``eval/answers.py``: the real ask pipeline, then
deepeval against each record's reference answer, with honest skips.

No cloud calls anywhere: the embedder, the answer LLM and the judge are
all local (Ollama, or the configured PGML embedder).
"""

from __future__ import annotations

import time
from typing import Protocol
from uuid import UUID

import asyncpg

from corpus_kb.contracts.model import DEFAULT_TENANT_ID
from corpus_kb.eval.answers import run_answer_section
from corpus_kb.eval.golden import GoldenRecord, golden_summary
from corpus_kb.eval.report import (
    STATUS_SUCCESS,
    EvalReport,
    RetrievalRow,
    empty_report,
    summary_from_rows,
)

__all__ = ["run_eval"]

# The nil project: research retrieval is project-scoped by design, so a
# project-less run sees an empty corpus. The CLI passes --project for
# assignment-built goldens (documented in docs/EVALUATION.md).
NIL_PROJECT_ID = "00000000-0000-0000-0000-000000000000"


class CachedEmbedder(Protocol):
    """The embedder surface the retrieval arm needs (U45 parity)."""

    async def embed_cached(self, tenant_id: UUID, text: str) -> list[float] | None: ...


def _build_embedder(cfg: dict[str, object], pool: asyncpg.Pool) -> CachedEmbedder:
    """The cached research embedder over the configured embedder."""
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.rag import create_embedder

    embedder: CachedEmbedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
    return embedder


async def run_eval(
    golden: list[GoldenRecord],
    cfg: dict[str, object],
    pool: asyncpg.Pool,
    k: int = 10,
    skip_answer_metrics: bool = False,
    judge: object | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    project_id: str = NIL_PROJECT_ID,
) -> EvalReport:
    """Score one golden set: retrieval for every record, answers where possible."""
    if not golden:
        return empty_report(k)
    rows = await _retrieval_section(golden, cfg, pool, k, tenant_id, project_id)
    outcome = await run_answer_section(golden, cfg, pool, skip_answer_metrics, judge)
    return EvalReport(
        status=STATUS_SUCCESS,
        golden=golden_summary(golden),
        retrieval=summary_from_rows(rows, k),
        answer=outcome.block,
        degraded_excluded=outcome.degraded_excluded,
    )


async def _retrieval_section(
    golden: list[GoldenRecord],
    cfg: dict[str, object],
    pool: asyncpg.Pool,
    k: int,
    tenant_id: str,
    project_id: str,
) -> list[RetrievalRow]:
    """Run the fused research arms per record; score with the U45 metrics."""
    from corpus_kb.research.retrieval_eval import _mrr, _ndcg, _recall_k
    from corpus_kb.research.retrieval_settings import load_retrieval_settings
    from corpus_kb.storage.tenant_conn import tenant_connection

    settings = load_retrieval_settings(cfg)
    depth = max(settings.vector_candidates, k * 2)
    embedder = _build_embedder(cfg, pool)
    rows: list[RetrievalRow] = []
    async with tenant_connection(pool, tenant_id) as conn:
        for record in golden:
            relevant = frozenset(record.relevant_chunk_ids)
            start = time.perf_counter()
            ranked = await _ranked_chunks(
                conn, embedder, record.query, tenant_id, project_id, depth, settings.rrf_k
            )
            latency_ms = (time.perf_counter() - start) * 1000.0
            rows.append(
                RetrievalRow(
                    query=record.query,
                    relevant_count=len(relevant),
                    retrieved_count=len(ranked[:k]),
                    recall_at_k=_recall_k(ranked, relevant, k),
                    mrr=_mrr(ranked, relevant),
                    ndcg_at_10=_ndcg(ranked, relevant),
                    latency_ms=latency_ms,
                )
            )
    return rows


async def _ranked_chunks(
    conn: asyncpg.Connection,
    embedder: CachedEmbedder,
    query: str,
    tenant_id: str,
    project_id: str,
    depth: int,
    rrf_k: int,
) -> list[str]:
    """One fused ranking as chunk-id strings (the ids golden files carry)."""
    from corpus_kb.rag.embedder import instruct
    from corpus_kb.research.retrieval import ResearchQuery, _rrf, _unit_arms

    q = ResearchQuery(query=query, tenant_id=UUID(tenant_id), project_id=UUID(project_id), k=depth)
    vector = await embedder.embed_cached(UUID(tenant_id), instruct(query))
    dense, fts = await _unit_arms(conn, vector, q, depth)
    fused = await _rrf(conn, dense, fts, depth, rrf_k)
    return [str(payload["chunk_id"]) for payload in fused]
