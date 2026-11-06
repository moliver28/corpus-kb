"""Scoring for the calibration sweep: one candidate over the golden queries.

``score_candidate`` ingests the sample, resolves the golden labels, ranks
each query, and reduces the runs to mean metrics. Refusals are honest: a
degraded embedding (the zero-vector fallback) stops the scoring, so fake
numbers never reach the lock file. The types here are shared with the
statistics module; orchestration lives in ``calibrate``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from corpus_kb.eval.golden import GoldenRecord

if TYPE_CHECKING:
    from corpus_kb.analysis.method_register import MethodRegister
    from corpus_kb.analysis.question_router import RoutingEmbedder
    from corpus_kb.eval.routing_calibrate import RoutingGoldenRow
from corpus_kb.eval.labels import LabelIndex, resolve_relevance
from corpus_kb.profiles.calibrate_spec import CalibrateSpec

__all__ = [
    "ROUTING_DEGRADED_REFUSAL",
    "CandidateScore",
    "EngineFactory",
    "IngestedDoc",
    "QueryScore",
    "SweepEngine",
    "route_family",
    "score_candidate",
]

METRIC_DECIMALS = 6


@dataclass(frozen=True)
class IngestedDoc:
    """One ingested sample file: its doc id and the honest degraded flag."""

    doc_id: str
    degraded: bool


class SweepEngine(Protocol):
    """One candidate's scratch world: ingest, index, rank, discard.

    ``rank`` takes the candidate's query-time recipe overrides; the engine
    applies them at rank time, so one ingested corpus serves the whole
    family.
    """

    async def ingest(self, path: Path, strategy: str) -> IngestedDoc: ...

    async def label_index(self) -> LabelIndex: ...

    async def rank(
        self, query: str, k: int, overrides: Mapping[str, object] | None = None
    ) -> list[str]: ...

    async def close(self) -> None: ...


EngineFactory = Callable[[str], Awaitable[SweepEngine]]


@dataclass(frozen=True)
class CandidateScore:
    """One candidate's mean metrics over the scored golden queries."""

    candidate: str
    queries: int
    skipped: int
    recall_at_k: float
    mrr: float
    ndcg_at_10: float


@dataclass(frozen=True)
class QueryScore:
    """One query's outcome under one candidate: scalars plus the raw run.

    ``relevant`` and ``ranked`` feed the paired statistics (ranx needs the
    ranked lists, not the means); they never serialize into the lock.
    """

    candidate: str
    query: str
    recall_at_k: float
    mrr: float
    ndcg_at_10: float
    relevant: frozenset[str]
    ranked: tuple[str, ...]


async def score_candidate(
    engine: SweepEngine,
    candidate: str,
    sample_paths: Sequence[Path],
    golden: Sequence[GoldenRecord],
    spec: CalibrateSpec,
) -> tuple[CandidateScore | None, list[QueryScore], str | None]:
    """Score one candidate; a refusal comes back as (None, [], reason)."""
    from corpus_kb.research.retrieval_eval import _mrr, _ndcg, _recall_k

    for path in sample_paths:
        doc = await engine.ingest(path, candidate)
        if doc.degraded:
            return (
                None,
                [],
                (
                    "embedding degraded: the zero-vector fallback ran, so calibration "
                    "numbers would be fake. Bring the embedder up and retry."
                ),
            )
    index = await engine.label_index()
    recalls: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    rows: list[QueryScore] = []
    skipped = 0
    for record in golden:
        relevant = resolve_relevance(record, index)
        if not relevant:
            skipped += 1
            continue
        overrides = spec.recipes.get(candidate) or None
        ranked = await engine.rank(record.query, spec.k, overrides)
        recall = _recall_k(ranked, relevant, spec.k)
        mrr = _mrr(ranked, relevant)
        ndcg = _ndcg(ranked, relevant)
        recalls.append(recall)
        mrrs.append(mrr)
        ndcgs.append(ndcg)
        rows.append(
            QueryScore(
                candidate=candidate,
                query=record.query,
                recall_at_k=recall,
                mrr=mrr,
                ndcg_at_10=ndcg,
                relevant=relevant,
                ranked=tuple(ranked),
            )
        )
    queries = len(recalls)
    score = CandidateScore(
        candidate=candidate,
        queries=queries,
        skipped=skipped,
        recall_at_k=round(sum(recalls) / queries, METRIC_DECIMALS) if queries else 0.0,
        mrr=round(sum(mrrs) / queries, METRIC_DECIMALS) if queries else 0.0,
        ndcg_at_10=round(sum(ndcgs) / queries, METRIC_DECIMALS) if queries else 0.0,
    )
    return score, rows, None


# The zero-vector fallback makes every calibration number fake; the
# routing sweep refuses on the first phrase-mode row (honest refusal).
ROUTING_DEGRADED_REFUSAL = (
    "embedding degraded: the zero-vector fallback ran, so calibration "
    "numbers would be fake. Bring the embedder up and retry."
)


def route_family(
    golden: Sequence[RoutingGoldenRow],
    register: MethodRegister,
    embedder: RoutingEmbedder,
    family: Sequence[str],
    recipes: Mapping[str, Mapping[str, object]],
) -> tuple[list[CandidateScore], list[QueryScore], str | None]:
    """Route every golden row once per grid point; a degraded row refuses.

    The routing sweep's scorer (mirrors ``score_candidate`` for the
    ingest family): one candidate is one ``(threshold, margin)`` point.
    One recipe maps to the router settings block it names.
    """
    from corpus_kb.analysis.question_router import route_question

    scored: list[CandidateScore] = []
    query_rows: list[QueryScore] = []
    settings: dict[str, object] = {}
    for name in family:
        recipe = recipes[name]
        settings["confidence_threshold"] = recipe["analysis.routing.confidence_threshold"]
        settings["margin"] = recipe["analysis.routing.margin"]
        hits: list[float] = []
        rows: list[QueryScore] = []
        for record in golden:
            result = route_question(record.question, register, embedder, settings)
            if result.trace.mode == "phrase":
                return [], [], ROUTING_DEGRADED_REFUSAL
            chosen = result.intent.method_id
            hit = 1.0 if chosen == record.expected_method_ids[0] else 0.0
            hits.append(hit)
            rows.append(
                QueryScore(
                    candidate=name,
                    query=record.question,
                    recall_at_k=hit,
                    mrr=hit,
                    ndcg_at_10=hit,
                    relevant=frozenset(record.expected_method_ids),
                    ranked=(chosen,) if chosen is not None else (),
                )
            )
        queries = len(hits)
        mean = round(sum(hits) / queries, METRIC_DECIMALS) if queries else 0.0
        scored.append(
            CandidateScore(
                candidate=name,
                queries=queries,
                skipped=0,
                recall_at_k=mean,
                mrr=mean,
                ndcg_at_10=mean,
            )
        )
        query_rows.extend(rows)
    return scored, query_rows, None
