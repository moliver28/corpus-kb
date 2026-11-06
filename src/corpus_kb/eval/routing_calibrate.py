"""The routing calibration sweep: a (threshold, margin) grid, Holm-corrected.

Hosts the W5 routing family inside the calibrate STRUCTURE
(:func:`corpus_kb.eval.calibrate.run_calibration` semantics, same
outcomes, same Holm stats, same lock flow) as its own module — the
ingest sweep's file sits at the 250-line limit, so the routing sweep
lives beside it and reuses its pieces, never a second pipeline:

  * outcomes: ``success`` / ``no_significant_winner`` / ``insufficient_data``;
  * scoring: ``eval.scoring.route_family`` (the routing mirror of
    ``score_candidate``);
  * stats: ``eval.stats.compare_all`` (fisher p-values, Holm step-down);
  * lock: ``eval.lockfile.write_routing_lock`` (``build_lock`` plus the
    ``sweep: "routing"`` mark), applied afterwards by the ONE
    ``tools.calibrate_tools.calibrate_apply`` op through the recipes
    allow-list.

The sweep routes the golden questions once per grid point through the
LIVE embedding path. A phrase-mode row (the zero-vector fallback) or a
missing embedder refuses the whole sweep: phrase numbers would fake a
calibration. Callers skip when the embedder is down.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.analysis.question_router import RoutingEmbedder
from corpus_kb.errors import ValidationError
from corpus_kb.eval.calibrate import CalibrationResult
from corpus_kb.eval.lockfile import write_routing_lock
from corpus_kb.eval.recipes import MAX_CANDIDATES, validate_candidates
from corpus_kb.eval.scoring import CandidateScore, QueryScore, route_family
from corpus_kb.eval.stats import compare_all

__all__ = ["ROUTING_GRID", "RoutingGoldenRow", "run_routing_calibration"]

# The sweep's default family: the pinned defaults are the baseline, the
# challengers walk the threshold and the margin around them. Eight
# points fit the MAX_CANDIDATES family cap.
ROUTING_GRID: tuple[tuple[float, float], ...] = (
    (0.55, 0.10),
    (0.45, 0.10),
    (0.65, 0.10),
    (0.55, 0.05),
    (0.55, 0.15),
    (0.45, 0.05),
    (0.65, 0.15),
    (0.50, 0.10),
)

_THRESHOLD_KEY = "analysis.routing.confidence_threshold"
_MARGIN_KEY = "analysis.routing.margin"
_RULE = "primary accuracy on the routing golden bench"


@dataclass(frozen=True)
class RoutingGoldenRow:
    """One routing golden row: the question and the acceptable methods."""

    question: str
    expected_method_ids: tuple[str, ...]


def _grid_name(point: tuple[float, float]) -> str:
    """The deterministic candidate name for one grid point."""
    threshold, margin = point
    return f"t{round(threshold * 100):03d}_m{round(margin * 100):02d}"


def _routing_insufficient(reason: str) -> CalibrationResult:
    return CalibrationResult(status="insufficient_data", rule=_RULE, reason=reason)


async def run_routing_calibration(
    *,
    golden: Sequence[RoutingGoldenRow],
    register: MethodRegister,
    embedder: RoutingEmbedder | None,
    cfg: Mapping[str, object],
    golden_sha256: str,
    lock_path: Path | None = None,
    profile_name: str | None = None,
    profile_sha256: str | None = None,
    grid: Sequence[tuple[float, float]] | None = None,
    alpha: float = 0.05,
    min_queries: int = 30,
) -> CalibrationResult:
    """Sweep the (threshold, margin) grid over the routing golden bench.

    Same contract as the ingest calibration: candidates validate BEFORE
    any routing runs; the challenger p-values Holm-correct; ONLY a
    ``success`` writes the lock, whose ``proposed_overrides`` the
    existing ``calibrate_apply`` op carries into config.yaml.
    """
    points = list(grid) if grid is not None else list(ROUTING_GRID)
    if not points:
        return _routing_insufficient("the routing grid is empty")
    if len(points) > MAX_CANDIDATES:
        raise ValidationError(
            f"A routing sweep allows at most {MAX_CANDIDATES} candidates; got {len(points)}.",
            remediation=f"Trim the grid to {MAX_CANDIDATES} points or fewer.",
        )
    if not golden:
        return _routing_insufficient("the routing golden file has no records")
    if embedder is None:
        return _routing_insufficient(
            "the routing sweep needs the live embedding path; skip the sweep when "
            "the embedder is down, never a phrase-mode sweep"
        )
    family = [_grid_name(point) for point in points]
    if len(set(family)) != len(family):
        raise ValidationError(
            "The routing grid repeats a (threshold, margin) point.",
            remediation="Give each candidate one distinct (threshold, margin) pair.",
        )
    recipes: dict[str, dict[str, object]] = {
        name: {_THRESHOLD_KEY: point[0], _MARGIN_KEY: point[1]}
        for name, point in zip(family, points, strict=True)
    }
    validate_candidates(recipes)
    scored, query_rows, refusal = route_family(golden, register, embedder, family, recipes)
    if refusal is not None:
        return _routing_insufficient(refusal)
    if not scored or all(row.queries == 0 for row in scored):
        return _routing_insufficient("no golden question routed for any candidate")
    thin = sorted(row.candidate for row in scored if row.queries < min_queries)
    if thin:
        return _routing_insufficient(
            f"{', '.join(thin[:3])} scored fewer than {min_queries} questions;"
            " add golden rows that route, or lower min_queries"
        )
    return _routing_compare_and_lock(
        scored=scored,
        query_rows=query_rows,
        family=family,
        recipes=recipes,
        cfg=cfg,
        golden_sha256=golden_sha256,
        lock_path=lock_path,
        profile_name=profile_name,
        profile_sha256=profile_sha256,
        alpha=alpha,
        min_queries=min_queries,
    )


def _routing_compare_and_lock(
    *,
    scored: list[CandidateScore],
    query_rows: list[QueryScore],
    family: list[str],
    recipes: dict[str, dict[str, object]],
    cfg: Mapping[str, object],
    golden_sha256: str,
    lock_path: Path | None,
    profile_name: str | None,
    profile_sha256: str | None,
    alpha: float,
    min_queries: int,
) -> CalibrationResult:
    """Holm-correct the family, pick the winner, write the lock on success."""
    family_scores: dict[str, list[QueryScore]] = {}
    for row in query_rows:
        family_scores.setdefault(row.candidate, []).append(row)
    outcomes = compare_all(family_scores, family[0], metric="ndcg", alpha=alpha)
    if any(outcome.p_value is None for outcome in outcomes):
        return _routing_insufficient(
            "the sign test ran (the ranx extra is missing), so no p-value and"
            " no winner can be claimed; install the calibrate extra and retry"
        )
    winners = [outcome for outcome in outcomes if outcome.significant and outcome.delta > 0]
    if not winners:
        return CalibrationResult(
            status="no_significant_winner",
            rule=_RULE,
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
    winner_overrides: dict[str, object] = dict(recipes[chosen])
    summary: dict[str, object] = {}
    if lock_path is not None:
        summary = write_routing_lock(
            scored=scored,
            chosen=chosen,
            winner_overrides=winner_overrides,
            family=family,
            recipes=recipes,
            cfg=cfg,
            golden_sha256=golden_sha256,
            lock_path=lock_path,
            profile_name=profile_name,
            profile_sha256=profile_sha256,
            alpha=alpha,
            min_queries=min_queries,
        )
    return CalibrationResult(
        status="success",
        rule=_RULE,
        candidates=tuple(scored),
        chosen=chosen,
        summary=summary,
        query_scores=tuple(query_rows),
    )
