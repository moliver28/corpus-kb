"""Calibrate-then-lock: baseline ONE ingestion strategy over a labeled sample.

``validate=calibrate`` on an ingest whose profile carries a ``calibrate:``
block ingests the sample once for the single configured candidate into a
fresh scratch engine, scores the golden queries, and writes
``<profile>.lock.json`` next to the profile. The authored YAML is never
touched. The sweep is a single-candidate baseline tool today: it refuses
more than one candidate, because transcript ingests share one strategy
axis and the candidates would tie. Multi-candidate comparison is tracked
in the follow-up issue.

Refusals are honest: degraded embeddings (zero-vector fallback) or labels
that match nothing answer ``insufficient_data`` and write no lock, because
numbers built on degraded vectors would be fake. This module owns the
ORCHESTRATION only; the engine (scratch storage + retrieval) is injected,
so tests run hermetically and the production engine lives in the tools
layer next to the pipeline it drives.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from corpus_kb.errors import ValidationError
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import LabelIndex, overlap_rule, resolve_relevance
from corpus_kb.eval.lockfile import build_lock
from corpus_kb.profiles.loader import CalibrateSpec

__all__ = [
    "CalibrationResult",
    "CandidateScore",
    "IngestedDoc",
    "QueryScore",
    "SweepEngine",
    "run_calibration",
]

METRIC_DECIMALS = 6


@dataclass(frozen=True)
class IngestedDoc:
    """One ingested sample file: its doc id and the honest degraded flag."""

    doc_id: str
    degraded: bool


class SweepEngine(Protocol):
    """One candidate's scratch world: ingest, index, rank, discard."""

    async def ingest(self, path: Path, strategy: str) -> IngestedDoc: ...

    async def label_index(self) -> LabelIndex: ...

    async def rank(self, query: str, k: int) -> list[str]: ...

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


@dataclass(frozen=True)
class CalibrationResult:
    """The sweep outcome: ``success`` carries a lock; refusals carry a reason."""

    status: str
    reason: str | None
    rule: str
    candidates: tuple[CandidateScore, ...]
    chosen: str | None
    summary: dict[str, object]
    query_scores: tuple[QueryScore, ...] = ()


async def run_calibration(
    spec: CalibrateSpec,
    *,
    golden: Sequence[GoldenRecord],
    golden_sha256: str,
    sample_paths: Sequence[Path],
    engine_factory: EngineFactory,
    cfg: Mapping[str, object],
    profile_name: str,
    profile_sha256: str,
    lock_path: Path | None = None,
    known_candidates: Collection[str] | None = None,
) -> CalibrationResult:
    """Baseline the single candidate, score the golden queries, write the lock.

    Candidates are checked against the registry (or ``known_candidates``)
    BEFORE any engine is built, so a typo never burns a sweep. Exactly one
    candidate is allowed: this is a baseline tool, not a comparison sweep.
    """
    if not golden:
        return _insufficient("the golden file has no records", spec.min_overlap)
    allowed = sorted(known_candidates) if known_candidates is not None else _registry_names()
    unknown = [name for name in spec.candidates if name not in set(allowed)]
    if unknown:
        examples = ", ".join(allowed[:6])
        raise ValidationError(
            f"calibrate.candidates names unknown strategies: {', '.join(unknown)}.",
            remediation=f"Use registered strategies, e.g. {examples}.",
        )
    if len(spec.candidates) > 1:
        raise ValidationError(
            "Multi-candidate sweeps are not available yet: transcript ingests"
            " share one strategy axis, so the candidates would tie.",
            remediation=(
                "Sweep one candidate to baseline it; multi-candidate comparison"
                " is tracked in the follow-up issue."
            ),
        )
    rule = overlap_rule(spec.min_overlap)
    scored: list[CandidateScore] = []
    query_rows: list[QueryScore] = []
    for candidate in spec.candidates:
        engine = await engine_factory(candidate)
        try:
            score, rows, refusal = await _score_candidate(
                engine, candidate, sample_paths, golden, spec
            )
        finally:
            await engine.close()
        if refusal is not None:
            return _insufficient(refusal, spec.min_overlap)
        assert score is not None
        scored.append(score)
        query_rows.extend(rows)
    if not scored or all(row.queries == 0 for row in scored):
        return _insufficient(
            "no golden label matched any ingested chunk; add spans or units that hit the sample",
            spec.min_overlap,
        )
    ranked_rows = sorted(scored, key=lambda row: (-row.ndcg_at_10, -row.recall_at_k, row.candidate))
    chosen = ranked_rows[0].candidate
    lock = build_lock(
        spec=spec,
        rows=scored,
        chosen=chosen,
        cfg=cfg,
        profile_name=profile_name,
        profile_sha256=profile_sha256,
        golden_sha256=golden_sha256,
        sample_paths=sample_paths,
    )
    if lock_path is not None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(lock, indent=2, sort_keys=True) + "\n"
        tmp_path = lock_path.with_suffix(lock_path.suffix + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(lock_path)
    summary = {key: value for key, value in lock.items() if key != "date"}
    return CalibrationResult(
        status="success",
        reason=None,
        rule=rule,
        candidates=tuple(scored),
        chosen=chosen,
        summary=summary,
        query_scores=tuple(query_rows),
    )


async def _score_candidate(
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
        ranked = await engine.rank(record.query, spec.k)
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


def _insufficient(reason: str, min_overlap: float) -> CalibrationResult:
    return CalibrationResult(
        status="insufficient_data",
        reason=reason,
        rule=overlap_rule(min_overlap),
        candidates=(),
        chosen=None,
        summary={},
    )


def _registry_names() -> list[str]:
    from corpus_kb.ingestion.registry import strategy_names

    return strategy_names()
