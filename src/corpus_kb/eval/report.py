"""The eval report: one dataclass plus the honest envelope serialization.

The envelope is the CLI's ``--json`` contract (house style: the exact
shape, no reshaping at the surface):

    {
      "status": "success" | "empty",
      "result": {
        "golden":   {"records", "with_answer", "without_answer"},
        "retrieval": {"recall_at_k", "mrr", "ndcg_at_10", "k",
                      "n_records", "p95_latency_ms", "per_record": [...]},
        "answer":   {"n_scored", "faithfulness", "answer_relevancy",
                     "contextual_recall", "contextual_precision"}
                  | {"skipped": "<honest reason>"},
      },
      "degraded_excluded": {"count", "reasons"},
    }

An empty golden file is status ``empty`` — an honest outcome, not an
error. A skipped answer section carries the reason string (the flag was
set, the extra is missing, or no record carries a reference answer).
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["EvalReport", "RetrievalRow", "RetrievalSummary", "to_envelope"]

STATUS_SUCCESS = "success"
STATUS_EMPTY = "empty"

# The remediation string reported when the [eval] extra is absent. Kept
# here so runner and adapter cannot drift apart on it.
EVAL_EXTRA_REMEDIATION = "pip install 'corpus-kb[eval]'"


@dataclass(frozen=True)
class RetrievalRow:
    """Per-record retrieval scores (the same metrics the U45 arms report)."""

    query: str
    relevant_count: int
    retrieved_count: int
    recall_at_k: float
    mrr: float
    ndcg_at_10: float
    latency_ms: float


@dataclass(frozen=True)
class RetrievalSummary:
    """Mean retrieval quality over the labeled records."""

    recall_at_k: float
    mrr: float
    ndcg_at_10: float
    k: int
    n_records: int
    p95_latency_ms: float
    per_record: list[RetrievalRow] = field(default_factory=list)


@dataclass(frozen=True)
class EvalReport:
    """The run's outcome, ready for :func:`to_envelope`.

    ``answer`` holds the metric dict when scored, or a
    ``{"skipped": reason}`` map when honestly skipped.
    """

    status: str
    golden: dict[str, int]
    retrieval: RetrievalSummary
    answer: dict[str, object]
    degraded_excluded: dict[str, object]


def empty_report(k: int) -> EvalReport:
    """The honest no-data report for an empty golden file."""
    return EvalReport(
        status=STATUS_EMPTY,
        golden={"records": 0, "with_answer": 0, "without_answer": 0},
        retrieval=RetrievalSummary(
            recall_at_k=0.0,
            mrr=0.0,
            ndcg_at_10=0.0,
            k=k,
            n_records=0,
            p95_latency_ms=0.0,
            per_record=[],
        ),
        answer={"skipped": "the golden set has no records"},
        degraded_excluded={"count": 0, "reasons": []},
    )


def summary_from_rows(rows: list[RetrievalRow], k: int) -> RetrievalSummary:
    """Aggregate the per-record rows into means and a p95 latency.

    The aggregation mirrors ``evaluate_arms``: plain means over records,
    the 95th percentile over recorded latencies.
    """
    import numpy as np

    recalls = [row.recall_at_k for row in rows]
    mrrs = [row.mrr for row in rows]
    ndcgs = [row.ndcg_at_10 for row in rows]
    latencies = [row.latency_ms for row in rows]
    return RetrievalSummary(
        recall_at_k=float(np.mean(recalls)) if recalls else 0.0,
        mrr=float(np.mean(mrrs)) if mrrs else 0.0,
        ndcg_at_10=float(np.mean(ndcgs)) if ndcgs else 0.0,
        k=k,
        n_records=len(rows),
        p95_latency_ms=float(np.percentile(latencies, 95)) if latencies else 0.0,
        per_record=list(rows),
    )


def to_envelope(report: EvalReport) -> dict[str, object]:
    """Serialize the report into the CLI ``--json`` envelope."""
    return {
        "status": report.status,
        "result": {
            "golden": dict(report.golden),
            "retrieval": _retrieval_dict(report.retrieval),
            "answer": dict(report.answer),
        },
        "degraded_excluded": dict(report.degraded_excluded),
    }


def _retrieval_dict(summary: RetrievalSummary) -> dict[str, object]:
    return {
        "recall_at_k": summary.recall_at_k,
        "mrr": summary.mrr,
        "ndcg_at_10": summary.ndcg_at_10,
        "k": summary.k,
        "n_records": summary.n_records,
        "p95_latency_ms": summary.p95_latency_ms,
        "per_record": [
            {
                "query": row.query,
                "relevant_count": row.relevant_count,
                "retrieved_count": row.retrieved_count,
                "recall_at_k": row.recall_at_k,
                "mrr": row.mrr,
                "ndcg_at_10": row.ndcg_at_10,
                "latency_ms": row.latency_ms,
            }
            for row in summary.per_record
        ],
    }
