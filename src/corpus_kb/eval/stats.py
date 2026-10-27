"""Paired statistics and report artifacts (unified-ingestion Wave 7).

The calibration sweep reports mean metrics per candidate; this module says
whether a difference is real. Two methods, named honestly in every result:

- ``fisher randomization (ranx)`` when the optional ``calibrate`` extra is
  installed — the paired test the eval protocol pre-registers;
- ``sign test`` otherwise — distribution-free, no p-value claim, stated
  as such so no reader mistakes it for the ranx result.

Artifacts: a JSON report (the lock summary plus every paired outcome) and
TREC-format qrels/run files, so a human can re-score every number on
another machine with standard tools.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from corpus_kb.eval.calibrate import QueryScore
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import LabelIndex, resolve_relevance

__all__ = [
    "PairOutcome",
    "paired_comparison",
    "write_report",
    "write_trec_qrels",
    "write_trec_run",
]

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


def paired_comparison(
    query_scores: Mapping[str, list[QueryScore]],
    baseline: str,
    challenger: str,
    metric: str = "ndcg",
    k: int | None = None,
) -> PairOutcome:
    """Compare two candidates on one metric over the same queries.

    The queries are paired by position: the sweep scores every candidate
    against the same golden order. The ranx fisher test needs the ranked
    lists; without the extra the sign test answers from the per-query
    scalars and reports no p-value.
    """
    field = METRIC_FIELDS[metric]
    base_rows = query_scores[baseline]
    challenge_rows = query_scores[challenger]
    base_values = [float(getattr(row, field)) for row in base_rows]
    challenge_values = [float(getattr(row, field)) for row in challenge_rows]
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


def write_trec_qrels(
    path: Path,
    golden: Sequence[GoldenRecord],
    index: LabelIndex,
    min_overlap: float,
) -> int:
    """TREC qrels from the golden labels: ``qid 0 docid gain`` per line.

    Labels resolve through the SAME rules the sweep scored with, so the
    exported file and the reported numbers can never disagree. Returns
    the number of written lines.
    """
    lines: list[str] = []
    for position, record in enumerate(golden, start=1):
        relevant = sorted(resolve_relevance(record, LabelIndex(index.entries, min_overlap)))
        for doc_id in relevant:
            lines.append(f"q{position} 0 {doc_id} 1")
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return len(lines)


def write_trec_run(path: Path, rows: list[QueryScore]) -> int:
    """TREC run for one candidate: ``qid Q0 docid rank score`` per line."""
    lines: list[str] = []
    for position, row in enumerate(rows, start=1):
        for rank, doc_id in enumerate(row.ranked, start=1):
            score = f"{1.0 / rank:.6f}"
            lines.append(f"q{position} Q0 {doc_id} {rank} {score}")
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return len(lines)


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
