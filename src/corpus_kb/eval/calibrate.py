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
ORCHESTRATION only — scoring lives in ``scoring``, and the engine
(scratch storage + retrieval) is injected, so tests run hermetically and
the production engine lives in the tools layer next to the pipeline it
drives.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from corpus_kb.errors import ValidationError
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import overlap_rule
from corpus_kb.eval.scoring import CandidateScore, EngineFactory, QueryScore, score_candidate
from corpus_kb.profiles.calibrate_spec import CalibrateSpec

__all__ = [
    "CalibrationResult",
    "run_calibration",
]


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
            score, rows, refusal = await score_candidate(
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
    # Imported here, not at module level: lockfile imports calibrate types
    # under TYPE_CHECKING, and a module-level edge back would form a cycle.
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
