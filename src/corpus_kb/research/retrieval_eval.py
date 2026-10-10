"""U45: retrieval evaluation harness over human-reviewed codings as labels.

Four arms — vector-only, FTS-only, RRF, RRF+reranker — are scored with
recall@k, MRR, nDCG@10 and p95 latency. Relevance labels come ONLY from
human-reviewed assignments (``research_assignments.status IN
('confirmed','overridden')``): a unit is relevant to its code's query
(code name + brief definition). With no reviewed codings every function
returns ``insufficient_data`` — numbers are never fabricated (v8 ground
rule 9).

The vector arm reuses ``research/retrieval.py`` internals so it inherits
the U40 MATERIALIZED-CTE pattern and the tenant/project scoping verbatim.
Default flips are NOT decided here: :func:`recommend_default` only speaks
when a measured report proves RRF >= vector-only on recall@k inside the
latency budget; otherwise it reports ``insufficient_data`` and the shipped
defaults stand.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import asyncpg
import numpy as np

from corpus_kb.research.retrieval import (
    ResearchQuery,
    _rrf,
    _unit_arms,
)
from corpus_kb.research.retrieval_settings import RetrievalSettings

ARMS = ("vector", "fts", "rrf", "rrf_rerank")
INSUFFICIENT_DATA = "insufficient_data"


class _Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float] | None: ...


class _Embedder(Protocol):
    """Structural slice of ResearchEmbedder the vector arm needs."""

    async def embed_cached(self, tenant_id: UUID, text: str) -> list[float] | None: ...


@dataclass(frozen=True)
class EvalQuery:
    """One labeled query: code-derived text + human-reviewed relevant units."""

    query_id: str
    text: str
    relevant: frozenset[int]


@dataclass(frozen=True)
class ArmMetrics:
    recall_at_k: float
    mrr: float
    ndcg_at_10: float
    p95_latency_ms: float
    n_queries: int


@dataclass(frozen=True)
class EvalReport:
    status: str
    k: int
    arms: dict[str, ArmMetrics] = field(default_factory=dict)


def insufficient_report() -> EvalReport:
    return EvalReport(status=INSUFFICIENT_DATA, k=0, arms={})


async def load_reviewed_labels(
    pool: asyncpg.Pool, tenant_id: str, project_id: str
) -> list[EvalQuery]:
    """Derive labeled queries from human-reviewed codings (empty = no data)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT cr.code_id, cr.name, cr.brief_definition,
                   array_agg(DISTINCT ra.unit_id) AS unit_ids
            FROM research_assignments ra
            JOIN code_registry cr
              ON cr.code_id = ra.code_id AND cr.tenant_id = ra.tenant_id
            WHERE ra.tenant_id = $1 AND ra.status IN ('confirmed', 'overridden')
            GROUP BY cr.code_id, cr.name, cr.brief_definition
            """,
            tenant_id,
            project_id,
        )
    return [
        EvalQuery(
            query_id=str(row["code_id"]),
            text=f"{row['name']}. {row['brief_definition']}",
            relevant=frozenset(int(u) for u in row["unit_ids"]),
        )
        for row in rows
    ]


def _recall_k(ranked: list[int], relevant: frozenset[int], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def _mrr(ranked: list[int], relevant: frozenset[int]) -> float:
    for pos, unit_id in enumerate(ranked, start=1):
        if unit_id in relevant:
            return 1.0 / pos
    return 0.0


def _ndcg(ranked: list[int], relevant: frozenset[int], k: int = 10) -> float:
    if not relevant:
        return 0.0
    dcg = sum(
        1.0 / np.log2(pos + 2)
        for pos, unit_id in enumerate(ranked[:k])
        if unit_id in relevant
    )
    ideal = sum(1.0 / np.log2(pos + 2) for pos in range(min(len(relevant), k)))
    return float(dcg / ideal) if ideal > 0 else 0.0


def _ranked_unit_ids(payloads: list[dict[str, object]]) -> list[int]:
    return [int(p["_unit_id"]) for p in payloads if p.get("_unit_id") is not None]


async def _run_arm(
    arm: str,
    conn: asyncpg.Connection,
    query_vector: list[float] | None,
    q: ResearchQuery,
    depth: int,
    reranker: _Reranker | None,
    query_text: str,
) -> list[int]:
    dense, fts = await _unit_arms(conn, query_vector, q, depth)
    if arm == "vector":
        return _ranked_unit_ids(dense)
    if arm == "fts":
        return _ranked_unit_ids(fts)
    fused = await _rrf(conn, dense, fts, depth)
    if arm == "rrf":
        return _ranked_unit_ids(fused)
    if reranker is None or not fused:
        return _ranked_unit_ids(fused)
    scores = reranker.score(query_text, [str(p["text"]) for p in fused])
    if scores is None:
        return _ranked_unit_ids(fused)
    ranked = sorted(zip(fused, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    return _ranked_unit_ids([payload for payload, _ in ranked])


async def evaluate_arms(
    pool: asyncpg.Pool,
    embedder: _Embedder,
    queries: list[EvalQuery],
    tenant_id: str,
    project_id: str,
    settings: RetrievalSettings,
    reranker: _Reranker | None = None,
    k: int = 10,
    arms: tuple[str, ...] = ARMS,
) -> EvalReport:
    """Run the retrieval arms over labeled queries and score them.

    ``insufficient_data`` (empty report) when no labeled queries exist.
    """
    if not queries:
        return insufficient_report()
    from corpus_kb.rag.embedder import instruct
    from corpus_kb.storage.tenant_conn import tenant_connection

    depth = max(settings.candidates_per_branch, k * 2)
    latencies: dict[str, list[float]] = {arm: [] for arm in arms}
    recalls: dict[str, list[float]] = {arm: [] for arm in arms}
    mrrs: dict[str, list[float]] = {arm: [] for arm in arms}
    ndcgs: dict[str, list[float]] = {arm: [] for arm in arms}

    async with tenant_connection(pool, tenant_id) as conn:
        for eq in queries:
            q = ResearchQuery(
                query=eq.text,
                tenant_id=UUID(tenant_id),
                project_id=UUID(project_id),
                k=depth,
                rerank=False,
            )
            vector = None
            if arm_needs_vector(arms):
                vector = await embedder.embed_cached(UUID(tenant_id), instruct(eq.text))
            for arm in arms:
                start = time.perf_counter()
                ranked = await _run_arm(arm, conn, vector, q, depth, reranker, eq.text)
                latencies[arm].append((time.perf_counter() - start) * 1000.0)
                recalls[arm].append(_recall_k(ranked, eq.relevant, k))
                mrrs[arm].append(_mrr(ranked, eq.relevant))
                ndcgs[arm].append(_ndcg(ranked, eq.relevant))

    metrics = {
        arm: ArmMetrics(
            recall_at_k=float(np.mean(recalls[arm])) if recalls[arm] else 0.0,
            mrr=float(np.mean(mrrs[arm])) if mrrs[arm] else 0.0,
            ndcg_at_10=float(np.mean(ndcgs[arm])) if ndcgs[arm] else 0.0,
            p95_latency_ms=float(np.percentile(latencies[arm], 95))
            if latencies[arm]
            else 0.0,
            n_queries=len(queries),
        )
        for arm in arms
    }
    return EvalReport(status="ok", k=k, arms=metrics)


def arm_needs_vector(arms: tuple[str, ...]) -> bool:
    return any(arm != "fts" for arm in arms)


def recommend_default(report: EvalReport, latency_budget_ms: float) -> str:
    """Default-flip gate: RRF only when it beats vector-only within budget.

    Anything less is ``insufficient_data`` — the shipped default stays put
    and the decision is documented as pending measurement.
    """
    if report.status != "ok":
        return INSUFFICIENT_DATA
    vector = report.arms.get("vector")
    rrf = report.arms.get("rrf")
    if vector is None or rrf is None or vector.n_queries == 0:
        return INSUFFICIENT_DATA
    rrf_ok = (
        rrf.recall_at_k >= vector.recall_at_k
        and rrf.p95_latency_ms <= latency_budget_ms
    )
    return "rrf" if rrf_ok else "vector"
