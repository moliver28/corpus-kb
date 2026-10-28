"""Calibrate-then-lock: sweep query-time recipes over a labeled sample.

``validate=calibrate`` on an ingest whose profile carries a ``calibrate:``
block runs a recipe family against one scratch engine: the sample is
ingested ONCE, then every candidate ranks cheaply through its own
query-time overrides. ``baseline`` (default: the first candidate) joins
the family automatically, and every challenger must beat it.
``compare_all`` Holm-corrects the paired p-values, so a big family
cannot win on a lucky uncorrected test.

Outcomes are honest. ``success`` means one challenger cleared the
corrected bar and ONLY then is the lock written.
``no_significant_winner`` means the family ran and nothing cleared it.
``insufficient_data`` covers degraded embeddings, labels that match
nothing, too few scored queries, or a missing p-value (the sign-test
path without ranx) — those numbers would be fake, so no lock is written.
Orchestration only: scoring lives in ``scoring``, stats in ``stats``,
the engine is injected, so tests run hermetically.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from corpus_kb.errors import ValidationError
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import overlap_rule
from corpus_kb.eval.scoring import CandidateScore, EngineFactory, QueryScore, score_candidate
from corpus_kb.eval.stats import compare_all
from corpus_kb.profiles.calibrate_spec import CalibrateSpec

__all__ = ["CalibrationResult", "run_calibration"]


@dataclass(frozen=True)
class CalibrationResult:
    """The sweep outcome: ``success`` carries a lock; refusals carry a reason."""

    status: str
    rule: str
    candidates: tuple[CandidateScore, ...] = ()
    chosen: str | None = None
    reason: str | None = None
    summary: dict[str, object] = field(default_factory=dict)
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
    """Sweep the recipe family; Holm-correct; write the lock on success.

    Candidates are checked BEFORE any engine is built, so a typo never
    burns a sweep. One engine serves the family: the sample is ingested
    once; every candidate ranks through its own overrides.
    """
    from corpus_kb.eval.recipes import MAX_CANDIDATES, validate_candidates

    if not golden:
        return _insufficient("the golden file has no records", spec.min_overlap)
    baseline = spec.baseline or (spec.candidates[0] if spec.candidates else None)
    if baseline is None:
        return _insufficient("the calibrate block lists no candidates", spec.min_overlap)
    family = list(spec.candidates)
    if baseline not in family:
        family.append(baseline)
    allowed = sorted(known_candidates) if known_candidates is not None else _registry_names()
    unknown = [name for name in family if name not in set(allowed)]
    if unknown:
        examples = ", ".join(allowed[:6])
        raise ValidationError(
            f"calibrate.candidates names unknown strategies: {', '.join(unknown)}.",
            remediation=f"Use registered strategies, e.g. {examples}.",
        )
    if len(family) > MAX_CANDIDATES:
        raise ValidationError(
            f"A sweep allows at most {MAX_CANDIDATES} candidates; got {len(family)}.",
            remediation=f"Trim the candidate list to {MAX_CANDIDATES} or fewer.",
        )
    validate_candidates({name: spec.recipes.get(name, {}) for name in family})
    rule = overlap_rule(spec.min_overlap)
    scored, query_rows, refusal = await _score_family(
        spec, family, golden, sample_paths, engine_factory
    )
    if refusal is not None:
        return _insufficient(refusal, spec.min_overlap)
    if not scored or all(row.queries == 0 for row in scored):
        return _insufficient(
            "no golden label matched any ingested chunk; add spans or units that hit the sample",
            spec.min_overlap,
        )
    thin = sorted(row.candidate for row in scored if row.queries < spec.min_queries)
    if thin:
        return _insufficient(
            f"{' , '.join(thin[:3])} scored fewer than {spec.min_queries} queries;"
            " add golden labels that hit the sample",
            spec.min_overlap,
        )
    return _compare_and_lock(
        spec,
        scored=scored,
        query_rows=query_rows,
        baseline=baseline,
        cfg=cfg,
        profile_name=profile_name,
        profile_sha256=profile_sha256,
        golden_sha256=golden_sha256,
        sample_paths=sample_paths,
        lock_path=lock_path,
        rule=rule,
    )


async def _score_family(
    spec: CalibrateSpec,
    family: list[str],
    golden: Sequence[GoldenRecord],
    sample_paths: Sequence[Path],
    engine_factory: EngineFactory,
) -> tuple[list[CandidateScore], list[QueryScore], str | None]:
    engine = await engine_factory(family[0])
    scored: list[CandidateScore] = []
    query_rows: list[QueryScore] = []
    try:
        for name in family:
            score, rows, refusal = await score_candidate(engine, name, sample_paths, golden, spec)
            if refusal is not None:
                return [], [], refusal
            assert score is not None
            scored.append(score)
            query_rows.extend(rows)
    finally:
        await engine.close()
    return scored, query_rows, None


def _compare_and_lock(
    spec: CalibrateSpec,
    *,
    scored: list[CandidateScore],
    query_rows: list[QueryScore],
    baseline: str,
    cfg: Mapping[str, object],
    profile_name: str,
    profile_sha256: str,
    golden_sha256: str,
    sample_paths: Sequence[Path],
    lock_path: Path | None,
    rule: str,
) -> CalibrationResult:
    """Holm-correct the family, pick the winner, write the lock on success."""
    family_scores: dict[str, list[QueryScore]] = {}
    for row in query_rows:
        family_scores.setdefault(row.candidate, []).append(row)
    outcomes = compare_all(family_scores, baseline, metric="ndcg", alpha=spec.alpha)
    if any(outcome.p_value is None for outcome in outcomes):
        return _insufficient(
            "the sign test ran (the ranx extra is missing), so no p-value and"
            " no winner can be claimed; install the calibrate extra and retry",
            spec.min_overlap,
        )
    winners = [outcome for outcome in outcomes if outcome.significant and outcome.delta > 0]
    if not winners:
        return CalibrationResult(
            status="no_significant_winner",
            rule=rule,
            candidates=tuple(scored),
            reason="No challenger beat the baseline beyond the corrected bar.",
            query_scores=tuple(query_rows),
        )
    by_name = {row.candidate: row for row in scored}
    best = min(
        winners,
        key=lambda outcome: (
            -by_name[outcome.challenger].ndcg_at_10,
            -by_name[outcome.challenger].recall_at_k,
            outcome.challenger,
        ),
    )
    chosen = best.challenger
    winner_overrides: dict[str, object] = dict(spec.recipes.get(chosen, {}))
    # Lazy import: lockfile imports calibrate types under TYPE_CHECKING.
    from corpus_kb.eval.lockfile import build_lock

    lock = build_lock(
        spec=spec,
        rows=scored,
        chosen=chosen,
        cfg=cfg,
        profile_name=profile_name,
        profile_sha256=profile_sha256,
        golden_sha256=golden_sha256,
        sample_paths=sample_paths,
        baseline=baseline,
        alpha=spec.alpha,
        min_queries=spec.min_queries,
        outcome="success",
        proposed_overrides=winner_overrides,
        reranker_active=_reranker_active(cfg, winner_overrides),
    )
    if lock_path is not None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = lock_path.with_suffix(lock_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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


def _reranker_active(cfg: Mapping[str, object], overrides: Mapping[str, object]) -> bool:
    """Whether the winner's recipe resolves to an enabled reranker."""
    from corpus_kb.eval.recipes import apply_overrides
    from corpus_kb.rag.rerank_settings import load_rerank_settings

    return load_rerank_settings(apply_overrides(dict(cfg), overrides)).enabled


def _insufficient(reason: str, min_overlap: float) -> CalibrationResult:
    return CalibrationResult(
        status="insufficient_data",
        rule=overlap_rule(min_overlap),
        reason=reason,
    )


def _registry_names() -> list[str]:
    from corpus_kb.ingestion.registry import strategy_names

    return strategy_names()
