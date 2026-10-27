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

from corpus_kb.errors import ValidationError
from corpus_kb.eval.calibrate import QueryScore
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import LabelIndex, resolve_relevance

__all__ = [
    "PairOutcome",
    "paired_comparison",
    "query_id_map",
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


def query_id_map(golden: Sequence[GoldenRecord]) -> dict[str, str]:
    """Map query text -> TREC qid (``q1``, ``q2``, ...), in golden order.

    One shared map keeps the qrels file and every run file aligned: the
    same query text gets the SAME qid in both files, so a skipped query
    can never shift the numbering apart.
    """
    return {record.query: f"q{position}" for position, record in enumerate(golden, start=1)}


def write_trec_qrels(
    path: Path,
    golden: Sequence[GoldenRecord],
    index: LabelIndex,
    min_overlap: float,
    *,
    qids: Mapping[str, str] | None = None,
) -> int:
    """TREC qrels from the golden labels: ``qid 0 docid gain`` per line.

    Labels resolve through the SAME rules the sweep scored with, so the
    exported file and the reported numbers can never disagree. Pass the
    ``query_id_map`` result as ``qids``; the default derives qids from the
    golden order. Returns the number of written lines.
    """
    qid_map = qids if qids is not None else query_id_map(golden)
    lines: list[str] = []
    for record in golden:
        relevant = sorted(resolve_relevance(record, LabelIndex(index.entries, min_overlap)))
        for doc_id in relevant:
            lines.append(f"{qid_map[record.query]} 0 {doc_id} 1")
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return len(lines)


def write_trec_run(
    path: Path,
    rows: list[QueryScore],
    *,
    qids: Mapping[str, str] | None = None,
) -> int:
    """TREC run for one candidate: ``qid Q0 docid rank score`` per line.

    Pass the SAME ``qids`` map the qrels file used (built by
    ``query_id_map`` over the golden records); the default derives qids
    from row order, which misaligns the files when a query was skipped.
    Returns the number of written lines.
    """
    qid_map = (
        qids
        if qids is not None
        else {row.query: f"q{position}" for position, row in enumerate(rows, start=1)}
    )
    lines: list[str] = []
    for row in rows:
        for rank, doc_id in enumerate(row.ranked, start=1):
            score = f"{1.0 / rank:.6f}"
            lines.append(f"{qid_map[row.query]} Q0 {doc_id} {rank} {score}")
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
