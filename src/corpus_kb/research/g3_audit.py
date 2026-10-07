"""G3 signal-validity harness (todo 16, v5 §12 G3 + r10/r11 extensions).

Metrics on a randomly-sampled audit set (reviewer overrides alone are
selection-biased — reviewers only see flagged items):
  * AUROC per signal (does the signal predict reviewer overrides?)
  * AURC + accuracy@coverage at 80/90/95% (Geifman & El-Yaniv 2017;
    arXiv:2407.01032 — risk-coverage is the correct metric family for a
    system with explicit rejection routing)
  * ECE per signal (temperature-scaled confidence, todo 14 machinery)
  * drop_non_predictive: signals below the AUROC floor are flagged for drop
  * LLM-vs-human Krippendorff's alpha on the gold subset against the
    human-human alpha ceiling: LLM alpha < 0.60 flips the code to
    human-only routing AND raises the ``human_parity_breach`` halt gate
    (todo 20 consumes the flip).

Report schema is pinned for todo 17. All numpy-only, offline.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from corpus_kb.coding.reliability import _alpha_from_units
from corpus_kb.research.calibration import expected_calibration_error

HUMAN_PARITY_FLOOR = 0.60
AUROC_DROP_FLOOR = 0.55
COVERAGE_LEVELS = (0.80, 0.90, 0.95)
HUMAN_PARITY_BREACH_GATE = "human_parity_breach"


def auroc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Rank-based AUROC (Mann-Whitney U / (n_pos * n_neg)); ties averaged.

    ``labels`` are 1 where the signal should score HIGH (e.g. the reviewer
    overrode the model). 0.5 is the no-information baseline.
    """
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=int)
    pos = scores[labels == 1]
    neg = scores[labels == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    sorted_scores = scores[order]
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    rank_sum_pos = float(ranks[labels == 1].sum())
    u = rank_sum_pos - len(pos) * (len(pos) + 1) / 2.0
    return float(u / (len(pos) * len(neg)))


def accuracy_at_coverage(scores: np.ndarray, labels: np.ndarray, coverage: float) -> float:
    """Accuracy on the top-``coverage`` fraction of units by score.

    The model abstains on the rest (explicit rejection routing), so the
    metric is accuracy over the retained set. Empty retained set -> 0.0.
    """
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=int)
    n = len(scores)
    if n == 0:
        return 0.0
    k = int(np.floor(coverage * n))
    if k <= 0:
        return 0.0
    order = np.argsort(-scores, kind="mergesort")
    retained = order[:k]
    return float(np.mean(labels[retained] == 1))


def aurc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Area under the risk-coverage curve (risk = error rate on retained).

    Units are retained in descending score order; risk at coverage k/n is
    1 - accuracy_at_coverage(k/n). Integrated over all coverage levels,
    normalized by n.
    """
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=int)
    n = len(scores)
    if n == 0:
        return 0.0
    order = np.argsort(-scores, kind="mergesort")
    errors = (labels[order] != 1).astype(np.float64)
    cumulative_errors = np.cumsum(errors)
    risks = cumulative_errors / np.arange(1, n + 1)
    return float(np.sum(risks) / n)


def signal_metrics(
    scores: np.ndarray, labels: np.ndarray, confidences: np.ndarray | None = None
) -> dict[str, float | int]:
    """The full r10 metric set for one signal against override labels.

    When ``confidences`` is given (rank-normalized signals are already in
    [0, 1] and thus usable as probabilities directly), ECE measures how well
    the signal calibrates as an override-probability estimator: bins compare
    mean predicted override probability against the empirical override rate.
    """
    metrics: dict[str, float | int] = {
        "auroc": auroc(scores, labels),
        "aurc": aurc(scores, labels),
        "n": len(scores),
    }
    for level in COVERAGE_LEVELS:
        metrics[f"acc_at_{int(level * 100)}"] = accuracy_at_coverage(scores, labels, level)
    if confidences is not None:
        metrics["ece"] = expected_calibration_error(
            np.asarray(confidences, dtype=np.float64), np.asarray(labels, dtype=np.float64)
        )
    return metrics


def two_rater_alpha(ratings_a: Sequence[int], ratings_b: Sequence[int]) -> float | None:
    """Krippendorff's alpha for two binary raters over shared items.

    Reuses the coding package's per-code nominal coincidence-matrix alpha
    (extend, don't duplicate). Each shared item contributes (n_u0, n_u1, m_u)
    = (zeros, ones, 2). Returns None below the alpha-computability floor
    (fewer than 2 items or a constant column).
    """
    if len(ratings_a) != len(ratings_b):
        raise ValueError("rater vectors must align")
    units = []
    for a, b in zip(ratings_a, ratings_b, strict=True):
        ones = int(a) + int(b)
        units.append((2 - ones, ones, 2))
    alpha, _n, _n0, _n1 = _alpha_from_units(units)
    return alpha


@dataclass(frozen=True)
class HumanParityVerdict:
    """Per-code parity verdict against the human-human alpha ceiling."""

    code_id: str
    llm_alpha: float | None
    human_alpha: float
    breach: bool
    human_only: bool


def human_parity_verdict(
    code_id: str,
    llm_alpha: float | None,
    human_alpha: float,
    floor: float = HUMAN_PARITY_FLOOR,
) -> HumanParityVerdict:
    """LLM-vs-human alpha below the floor flips the code to human-only."""
    breach = llm_alpha is None or llm_alpha < floor
    return HumanParityVerdict(
        code_id=code_id,
        llm_alpha=llm_alpha,
        human_alpha=human_alpha,
        breach=breach,
        human_only=breach,
    )


def drop_non_predictive(
    signal_metrics_by_name: dict[str, dict[str, float | int]],
    floor: float = AUROC_DROP_FLOOR,
) -> list[str]:
    """Names of signals whose AUROC does not clear the floor (drop list)."""
    return [
        name for name, metrics in signal_metrics_by_name.items() if float(metrics["auroc"]) < floor
    ]


@dataclass(frozen=True)
class AuditRow:
    """One audited (unit, top-code) observation on the randomly-sampled set."""

    code_id: str
    signal_values: dict[str, float]
    override: int
    llm_label: int
    human_label: int
    confidence: float = 0.0


@dataclass(frozen=True)
class G3Report:
    """Schema-pinned G3 artifact (todo 17 consumes this shape)."""

    n_audit: int
    signal_metrics: dict[str, dict[str, float | int]]
    combined_metrics: dict[str, float | int]
    dropped_signals: list[str]
    human_parity: list[HumanParityVerdict]
    parity_breach: bool
    halt_gate: str | None = None
    notes: list[str] = field(default_factory=list)


def run_g3_audit(
    rows: Sequence[AuditRow],
    signal_names: Sequence[str],
    human_alpha_ceiling: float = 1.0,
    confidence_columns: dict[str, Sequence[float]] | None = None,
) -> G3Report:
    """Compute the full G3 report from audit rows.

    Per-code rows feed the human-parity gate (LLM-vs-human alpha vs the
    MEASURED human-human alpha ceiling supplied by the caller); all rows
    feed the signal metrics (override = 1 where the reviewer overrode the
    model decision). A parity breach on ANY code sets halt_gate =
    ``human_parity_breach`` and flips that code to human-only routing
    immediately (r11 WIRED).

    ``confidence_columns`` optionally supplies per-signal probability-scale
    confidences (rank-normalized signals qualify) for the ECE term; raw
    signal scales (hedge counts, margins) are not probabilities and would
    distort calibration.
    """
    if not rows:
        return G3Report(0, {}, {}, [], [], False, None, ["empty audit set"])
    override_labels = np.array([r.override for r in rows], dtype=int)
    by_signal: dict[str, dict[str, float | int]] = {}
    for name in signal_names:
        values = np.array([float(r.signal_values.get(name, 0.0)) for r in rows])
        confidences: np.ndarray | None = None
        if confidence_columns and name in confidence_columns:
            confidences = np.array(confidence_columns[name], dtype=np.float64)
        by_signal[name] = signal_metrics(values, override_labels, confidences)
    combined_values = np.array(
        [sum(float(r.signal_values.get(n, 0.0)) for n in signal_names) for r in rows]
    )
    if confidence_columns and set(signal_names) <= set(confidence_columns):
        combined_conf = np.mean(
            np.array([confidence_columns[n] for n in signal_names], dtype=np.float64), axis=0
        )
    else:
        combined_conf = np.clip(combined_values / max(len(signal_names), 1), 0.0, 1.0)
    combined = signal_metrics(combined_values, override_labels, combined_conf)

    parity: list[HumanParityVerdict] = []
    code_ids = sorted({r.code_id for r in rows})
    for code_id in code_ids:
        code_rows = [r for r in rows if r.code_id == code_id]
        llm_alpha = two_rater_alpha(
            [r.llm_label for r in code_rows], [r.human_label for r in code_rows]
        )
        parity.append(human_parity_verdict(code_id, llm_alpha, human_alpha_ceiling))
    breach = any(v.breach for v in parity)
    return G3Report(
        n_audit=len(rows),
        signal_metrics=by_signal,
        combined_metrics=combined,
        dropped_signals=drop_non_predictive(by_signal),
        human_parity=parity,
        parity_breach=breach,
        halt_gate=HUMAN_PARITY_BREACH_GATE if breach else None,
    )
