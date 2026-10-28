"""Paired statistics for the calibration sweep (unified-ingestion Wave 7).

The sweep reports mean metrics per candidate; this module says whether a
difference is real, for the WHOLE family at once. Two methods, named
honestly in every result:

- ``fisher randomization (ranx)`` when the optional ``calibrate`` extra is
  installed — the paired test the eval protocol pre-registers;
- ``sign test`` otherwise — distribution-free, no p-value claim, stated
  as such so no reader mistakes it for the ranx result.

``compare_all`` runs every baseline-versus-challenger pairing and applies
a Holm step-down correction, so a five-candidate sweep cannot claim a
winner on a p-value that would not survive family-wise correction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from corpus_kb.errors import ValidationError
from corpus_kb.eval.scoring import QueryScore

__all__ = ["PairOutcome", "compare_all", "paired_comparison", "write_report"]

METRIC_FIELDS = {"recall": "recall_at_k", "mrr": "mrr", "ndcg": "ndcg_at_10"}


@dataclass(frozen=True)
class PairOutcome:
    """One baseline-versus-challenger comparison on one metric."""

    baseline: str
    challenger: str
    metric: str
    delta: float
    wins: int
    losses: int
    ties: int
    method: str
    p_value: float | None
    significant: bool = False


def paired_comparison(
    query_scores: Mapping[str, list[QueryScore]],
    baseline: str,
    challenger: str,
    metric: str = "ndcg",
    k: int | None = None,
) -> PairOutcome:
    """Compare two candidates on one metric over the same queries.

    Queries pair by QUERY TEXT, never by position: a candidate that skipped
    a query would otherwise shift every later pair. A differing query set
    is a hard error — the candidates are not comparable. The ranx fisher
    test needs the ranked lists; without the extra the sign test answers
    from the per-query scalars and reports no p-value.
    """
    field = METRIC_FIELDS[metric]
    base_by_query = {row.query: row for row in query_scores[baseline]}
    challenge_by_query = {row.query: row for row in query_scores[challenger]}
    if set(base_by_query) != set(challenge_by_query):
        only_base = sorted(set(base_by_query) - set(challenge_by_query))
        only_challenge = sorted(set(challenge_by_query) - set(base_by_query))
        raise ValidationError(
            "The two candidates were scored on different query sets.",
            remediation=(
                f"Queries only the baseline scored: {only_base[:5]}."
                f" Only the challenger scored: {only_challenge[:5]}."
                " Re-run the sweep so both candidates score the same queries."
            ),
        )
    queries = sorted(base_by_query)
    base_values = [float(getattr(base_by_query[query], field)) for query in queries]
    challenge_values = [float(getattr(challenge_by_query[query], field)) for query in queries]
    delta = (
        round(sum(challenge_values) / len(challenge_values), 6)
        - round(sum(base_values) / len(base_values), 6)
        if base_values
        else 0.0
    )
    wins = sum(1 for a, b in zip(challenge_values, base_values, strict=True) if a > b)
    losses = sum(1 for a, b in zip(challenge_values, base_values, strict=True) if a < b)
    ties = len(base_values) - wins - losses
    p_value = _ranx_p_value(query_scores, baseline, challenger, metric, k)
    if p_value is not None:
        return PairOutcome(
            baseline=baseline,
            challenger=challenger,
            metric=metric,
            delta=round(delta, 6),
            wins=wins,
            losses=losses,
            ties=ties,
            method="fisher randomization (ranx)",
            p_value=p_value,
        )
    return PairOutcome(
        baseline=baseline,
        challenger=challenger,
        metric=metric,
        delta=round(delta, 6),
        wins=wins,
        losses=losses,
        ties=ties,
        method="sign test (ranx not installed; no p-value claimed)",
        p_value=None,
    )


def compare_all(
    family_scores: Mapping[str, list[QueryScore]],
    baseline_name: str,
    metric: str = "ndcg",
    alpha: float = 0.05,
) -> list[PairOutcome]:
    """Compare every challenger against the baseline on one metric.

    Challengers run in name order (deterministic output). The p-values get
    a Holm step-down correction at ``alpha``. The baseline is never in the
    output — it is what every challenger is measured against.
    """
    if baseline_name not in family_scores:
        raise ValidationError(
            f"The baseline candidate {baseline_name!r} was not scored.",
            remediation="Score the baseline beside every challenger, then compare again.",
        )
    outcomes = [
        paired_comparison(family_scores, baseline_name, challenger, metric)
        for challenger in sorted(family_scores)
        if challenger != baseline_name
    ]
    return _holm_bonferroni(outcomes, alpha)


def _holm_bonferroni(outcomes: list[PairOutcome], alpha: float) -> list[PairOutcome]:
    """Holm step-down: smallest p first, each scaled by the tests left.

    The scaled values grow monotonically; at the first failure the walk
    stops and every later outcome is not significant, so a small raw
    p-value can never rescue a family the correction already rejected.
    """
    tested = sorted(
        (outcome for outcome in outcomes if outcome.p_value is not None),
        key=lambda outcome: (outcome.p_value, outcome.challenger),
    )
    adjusted = 0.0
    significant: dict[str, bool] = {}
    for position, outcome in enumerate(tested, start=1):
        assert outcome.p_value is not None
        adjusted = max(adjusted, (len(tested) - position + 1) * outcome.p_value)
        significant[outcome.challenger] = adjusted <= alpha
    return [
        replace(outcome, significant=significant.get(outcome.challenger, False))
        for outcome in outcomes
    ]


def _ranx_p_value(
    query_scores: Mapping[str, list[QueryScore]],
    baseline: str,
    challenger: str,
    metric: str,
    k: int | None,
) -> float | None:
    """The pairwise p-value from the ranx report, or None without ranx.

    The report keys models by name, so both candidates must appear; the
    comparison dict carries one p-value per metric string, and only the
    requested metric's value is read.
    """
    from corpus_kb.eval._ranx import fisher_comparison

    runs = {
        name: [(row.relevant, row.ranked) for row in query_scores[name]]
        for name in (baseline, challenger)
    }
    report = fisher_comparison(runs, k if k is not None else 10)
    if report is None:
        return None
    comparisons = report.get(challenger, {}).get("comparisons", {}).get(baseline, {})
    metric_key = _ranx_metric_key(report, metric)
    pair = comparisons.get(metric_key)
    # Report.to_dict() serializes the p-value as a bare float per metric.
    if isinstance(pair, dict):
        pair = pair.get("p_value")
    return float(pair) if isinstance(pair, (int, float)) else None


def _ranx_metric_key(report: Mapping[str, object], metric: str) -> str:
    """Match the report's metric strings (recall@k / ndcg@k / mrr)."""
    metrics = report.get("metrics")
    if isinstance(metrics, list):
        for candidate in metrics:
            if isinstance(candidate, str) and candidate.startswith(metric):
                return candidate
    return metric


def write_report(
    path: Path,
    *,
    summary: Mapping[str, object],
    outcomes: list[PairOutcome],
) -> None:
    """The JSON report: the lock summary plus every paired outcome."""
    payload = {
        "calibration": dict(summary),
        "paired": [asdict(outcome) for outcome in outcomes],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
