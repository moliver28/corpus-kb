"""The eval runner: one golden file, two scored sections.

RETRIEVAL — every record's query runs the research rank seam
(``research.retrieval.ranked_children``: dense + FTS arms, house RRF
fusion, no parent expansion), the same machinery the calibration sweeps
use. The ranked list of chunk-id UUIDs is scored with the U45 metric
functions (recall@k, MRR, nDCG@10) — the eval numbers are the same
species the research benchmarks report, computed by the same code.

ANSWER — delegated to ``eval/answers.py``: the real ask pipeline, then
deepeval against each record's reference answer, with honest skips.

No cloud calls anywhere: the embedder, the answer LLM and the judge are
all local (Ollama, or the configured PGML embedder).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
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

if TYPE_CHECKING:
    from corpus_kb.projections.research._embed import ResearchEmbedder

__all__ = ["run_eval"]

# The nil project: research retrieval is project-scoped by design, so a
# project-less run sees an empty corpus. The CLI passes --project for
# assignment-built goldens (documented in docs/EVALUATION.md).
NIL_PROJECT_ID = "00000000-0000-0000-0000-000000000000"


def _build_embedder(cfg: dict[str, object], pool: asyncpg.Pool) -> ResearchEmbedder:
    """The cached research embedder over the configured embedder."""
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.rag import create_embedder

    return ResearchEmbedder(pool, create_embedder(cfg, pool))


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
    """Rank through the research seam per record; score with U45 metrics."""
    from corpus_kb.research.retrieval import ResearchQuery, ranked_children
    from corpus_kb.research.retrieval_eval import _mrr, _ndcg, _recall_k
    from corpus_kb.research.retrieval_settings import load_retrieval_settings

    settings = load_retrieval_settings(cfg)
    embedder = _build_embedder(cfg, pool)
    rows: list[RetrievalRow] = []
    for record in golden:
        relevant = frozenset(record.relevant_chunk_ids)
        start = time.perf_counter()
        q = ResearchQuery(
            query=record.query,
            tenant_id=UUID(tenant_id),
            project_id=UUID(project_id),
            k=k,
            include_exchange_children=False,
        )
        fused = await ranked_children(pool, embedder, q, retrieval_settings=settings)
        ranked = [str(payload["chunk_id"]) for payload in fused]
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
