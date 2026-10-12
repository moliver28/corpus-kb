"""Risk-coverage thresholding (U41; v8 §3 U41, v6 §7 context).

From held-out (score, correctness) pairs: the selective risk vs coverage
curve, and the confidence threshold attaining an admin target risk on the
CALIBRATION split under an exact Clopper-Pearson upper confidence bound
(finite-sample-safe: the guarantee is not optimistic), with the realized
risk reported on a held-out TEST split.

Units scoring >= threshold are auto-accepted (and sampled for audit per
``audit.sample_rate``); units below route to human review. Without enough
labeled calibration data the result is ``insufficient_data`` and EVERYTHING
routes to review (threshold None is the sentinel the router reads).

Pure numpy/stdlib; the Clopper-Pearson bound is an exact binomial-tail
inversion (log-space pmf sums, bisection) — no scipy, no approximations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

OK = "ok"
INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class RiskCoverageCurve:
    """Selective risk as coverage grows (best-scored units accepted first)."""

    thresholds: np.ndarray  # descending candidate thresholds
    coverage: np.ndarray  # fraction auto-accepted at each threshold
    risk: np.ndarray  # selective risk (error rate among accepted)
    n_accepted: np.ndarray  # absolute accepted count at each threshold


@dataclass(frozen=True)
class ThresholdResult:
    """Gate-ready routing threshold (U41).

    ``status`` is ``ok`` or ``insufficient_data``; on insufficient data (or
    when no threshold attains the target within its bound) the threshold is
    None and the caller must route EVERYTHING to review.
    """

    status: str
    threshold: float | None
    target_risk: float
    cp_upper_bound: float | None  # exact upper bound on true risk at threshold
    calibration_risk: float | None
    test_risk: float | None  # realized risk on the held-out split
    test_coverage: float | None
    n_calibration: int
    n_test: int
    reason: str


def binomial_cdf(k: int, n: int, p: float) -> float:
    """Exact P(X <= k), X ~ Binom(n, p); log-space pmf sum (numpy/stdlib)."""
    if n <= 0:
        return 1.0
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    log_pmf = [
        math.lgamma(n + 1)
        - math.lgamma(i + 1)
        - math.lgamma(n - i + 1)
        + i * math.log(p)
        + (n - i) * math.log1p(-p)
        for i in range(k + 1)
    ]
    m = max(log_pmf)
    total = sum(math.exp(lp - m) for lp in log_pmf)
    return min(max(total * math.exp(m), 0.0), 1.0)


def clopper_pearson_upper(k: int, n: int, confidence: float = 0.95) -> float:
    """Exact one-sided upper confidence bound on a binomial proportion.

    Smallest U in [0, 1] with P(X <= k | Binom(n, U)) <= 1 - confidence,
    solved by bisection on the (monotone decreasing) exact CDF. k = n -> 1.0;
    n = 0 -> 1.0 (no data cannot bound risk below certainty).
    """
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    if k < 0:
        raise ValueError("k must be non-negative")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must lie in (0, 1)")
    target = 1.0 - confidence
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if binomial_cdf(k, n, mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2.0


def risk_coverage_curve(
    scores: np.ndarray, outcomes: np.ndarray, n_points: int | None = None
) -> RiskCoverageCurve:
    """Selective risk vs fraction auto-accepted over descending thresholds.

    Args:
        scores: Per-unit routing score (higher = accept).
        outcomes: 1 where the unit's label was WRONG (an error), 0 otherwise.
        n_points: Optional cap on evaluated thresholds (spaced quantiles of
            the score distribution; defaults to one per unique score).

    Returns:
        RiskCoverageCurve. Empty input yields an empty curve.
    """
    s = np.asarray(scores, dtype=float).ravel()
    o = np.asarray(outcomes, dtype=float).ravel()
    if s.size != o.size:
        raise ValueError("scores and outcomes must align")
    if s.size == 0:
        empty = np.array([], dtype=float)
        return RiskCoverageCurve(empty, empty.copy(), empty.copy(), empty.copy())
    order = np.argsort(-s, kind="stable")
    s_sorted = s[order]
    o_sorted = o[order]
    candidates = np.unique(s_sorted)[::-1]  # descending
    if n_points is not None and len(candidates) > n_points:
        candidates = np.unique(np.quantile(s_sorted, np.linspace(0.0, 1.0, n_points)))[::-1]
    counts = np.searchsorted(-s_sorted, -candidates, side="right").astype(np.int64)
    keep = counts > 0
    counts = counts[keep]
    candidates = candidates[keep]
    cum_err = np.cumsum(o_sorted)
    coverages = counts / s_sorted.size
    risks = cum_err[counts - 1] / counts
    return RiskCoverageCurve(
        thresholds=candidates,
        coverage=coverages,
        risk=risks,
        n_accepted=counts,
    )


def threshold_for_target_risk(
    scores_cal: np.ndarray,
    outcomes_cal: np.ndarray,
    scores_test: np.ndarray,
    outcomes_test: np.ndarray,
    *,
    target_risk: float = 0.05,
    confidence: float = 0.95,
    min_calibration_n: int = 30,
) -> ThresholdResult:
    """Calibration-split threshold attaining the target risk, CP-guarded.

    Chooses the LOWEST threshold whose exact Clopper-Pearson upper bound on
    the calibration risk is <= target_risk (so small samples cannot produce
    an optimistic guarantee), then reports realized risk on the TEST split.
    Fewer than ``min_calibration_n`` labeled calibration units -> status
    ``insufficient_data`` with threshold None: everything routes to review.
    """
    s_cal = np.asarray(scores_cal, dtype=float).ravel()
    o_cal = np.asarray(outcomes_cal, dtype=float).ravel()
    s_test = np.asarray(scores_test, dtype=float).ravel()
    o_test = np.asarray(outcomes_test, dtype=float).ravel()
    if s_cal.size != o_cal.size or s_test.size != o_test.size:
        raise ValueError("scores and outcomes must align per split")
    if s_cal.size < min_calibration_n:
        return ThresholdResult(
            status=INSUFFICIENT_DATA,
            threshold=None,
            target_risk=target_risk,
            cp_upper_bound=None,
            calibration_risk=None,
            test_risk=None,
            test_coverage=None,
            n_calibration=int(s_cal.size),
            n_test=int(s_test.size),
            reason=(
                f"only {s_cal.size} labeled calibration units; need >= "
                f"{min_calibration_n} before any auto-accept threshold - "
                f"routing everything to review"
            ),
        )
    curve = risk_coverage_curve(s_cal, o_cal)
    chosen: float | None = None
    chosen_risk: float | None = None
    chosen_cp: float | None = None
    for t, risk, n_acc in zip(curve.thresholds, curve.risk, curve.n_accepted, strict=True):
        cp = clopper_pearson_upper(round(float(risk) * int(n_acc)), int(n_acc), confidence)
        if cp <= target_risk:
            # Keep overwriting: thresholds descend, so the LAST qualifier is
            # the maximum-coverage threshold still inside the CP guarantee.
            chosen = float(t)
            chosen_risk = float(risk)
            chosen_cp = cp
    if chosen is None:
        return ThresholdResult(
            status=OK,
            threshold=None,
            target_risk=target_risk,
            cp_upper_bound=None,
            calibration_risk=None,
            test_risk=None,
            test_coverage=None,
            n_calibration=int(s_cal.size),
            n_test=int(s_test.size),
            reason=(
                f"no threshold on the calibration split attains target risk "
                f"{target_risk} within its Clopper-Pearson bound at "
                f"confidence {confidence} - routing everything to review"
            ),
        )
    test_mask = s_test >= chosen
    n_accept = int(np.sum(test_mask))
    test_risk = float(np.mean(o_test[test_mask])) if n_accept else None
    test_coverage = n_accept / s_test.size if s_test.size else None
    return ThresholdResult(
        status=OK,
        threshold=chosen,
        target_risk=target_risk,
        cp_upper_bound=chosen_cp,
        calibration_risk=chosen_risk,
        test_risk=test_risk,
        test_coverage=test_coverage,
        n_calibration=int(s_cal.size),
        n_test=int(s_test.size),
        reason=(
            f"threshold {chosen:.4f} attains calibration risk {chosen_risk:.4f} "
            f"with CP upper bound {chosen_cp:.4f} <= target {target_risk}"
        ),
    )
