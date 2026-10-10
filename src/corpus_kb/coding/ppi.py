"""Prediction-powered inference (U14; v6 §7 U14, v8 §2 B-context).

Small deterministic numpy estimator in the shape of Angelopoulos et al.
(2023): the full-population model predictions give the cheap estimate; a
small human-audited sample with known inclusion probabilities corrects the
model's bias:

    theta_hat = mean(f over all N) + sum_i w_i (y_i - f_i) / sum_i w_i,
    w_i = 1 / pi_i            (inverse-probability weights)

where f_i is the model prediction ON EACH AUDITED UNIT (passed separately —
the audit sample is not assumed to be the population prefix). The
confidence interval combines the two variance terms: the spread of the
model predictions over N (the population term) and the weighted spread of
the residuals over the audit sample (the correction term; Kish effective
sample size). With NO audit sample the raw estimate is reported with the
honest ``uncorrected`` label and a None interval — never "validated".

Pure functions; zero I/O; no randomness (the estimator is a closed form).
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

UNCORRECTED = "uncorrected"
CORRECTED = "corrected"
INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class PPIResult:
    """Dual-reported estimate: raw model estimate + PPI debiased estimate.

    ``label`` is ``uncorrected`` when no audit sample exists — the raw value
    must never be presented as validated (v6 §7 U14).
    """

    status: str  # corrected | uncorrected | insufficient_data
    estimate: float  # the debiased (or, when uncorrected, raw) point estimate
    raw_estimate: float
    label: str  # exactly "uncorrected" when no audit sample
    lower: float | None
    upper: float | None
    n_population: int
    n_audit: int
    effective_audit_n: float | None  # Kish effective sample size of the weights


def ppi_estimate(
    model_preds_all: np.ndarray,
    human_labels_sample: np.ndarray,
    model_preds_audited: np.ndarray,
    inclusion_probabilities: np.ndarray,
    *,
    confidence_level: float = 0.95,
) -> PPIResult:
    """Prediction-powered estimate of the population mean of a label.

    Args:
        model_preds_all: Model predictions f(x) over ALL N units (values in
            [0, 1] for prevalences, or any continuous scale).
        human_labels_sample: Human labels y over the n audited units.
        model_preds_audited: The model's prediction f_i for EACH audited
            unit, aligned row-for-row with ``human_labels_sample``.
        inclusion_probabilities: Design inclusion probability pi_i for each
            audited unit (from the audit_partitions design, U17); in (0, 1].
        confidence_level: Two-sided interval coverage for the corrected CI.

    Returns:
        PPIResult. Empty audit sample -> status/label ``uncorrected`` with
        None bounds; audit sample with n < 2 -> ``insufficient_data`` (a
        one-unit audit carries no usable correction variance).
    """
    f = np.asarray(model_preds_all, dtype=float).ravel()
    y = np.asarray(human_labels_sample, dtype=float).ravel()
    f_aud = np.asarray(model_preds_audited, dtype=float).ravel()
    pi = np.asarray(inclusion_probabilities, dtype=float).ravel()
    if f.size == 0:
        raise ValueError("model_preds_all must be non-empty")
    if not (y.size == pi.size == f_aud.size):
        raise ValueError(
            "human_labels_sample, model_preds_audited and inclusion_probabilities must align"
        )
    raw = float(np.mean(f))

    if y.size == 0:
        return PPIResult(
            status=UNCORRECTED,
            estimate=raw,
            raw_estimate=raw,
            label=UNCORRECTED,
            lower=None,
            upper=None,
            n_population=int(f.size),
            n_audit=0,
            effective_audit_n=None,
        )
    if np.any(pi <= 0.0) or np.any(pi > 1.0):
        raise ValueError("inclusion probabilities must lie in (0, 1]")
    if y.size < 2:
        return PPIResult(
            status=INSUFFICIENT_DATA,
            estimate=raw,
            raw_estimate=raw,
            label=UNCORRECTED,
            lower=None,
            upper=None,
            n_population=int(f.size),
            n_audit=int(y.size),
            effective_audit_n=None,
        )

    w = 1.0 / pi
    residuals = y - f_aud
    w_sum = float(np.sum(w))
    correction = float(np.sum(w * residuals) / w_sum)
    estimate = raw + correction

    # Population term: variance of the model mean over N.
    var_model = float(np.var(f, ddof=1)) / f.size
    # Correction term: weighted residual variance / Kish effective n.
    n = y.size
    residuals_bar = float(np.sum(w * residuals) / w_sum)
    var_residual = float(np.sum(w * (residuals - residuals_bar) ** 2) / w_sum)
    n_eff = float(w_sum**2 / np.sum(w**2))
    var_correction = var_residual / n_eff
    se = float(np.sqrt(max(var_model + var_correction, 0.0)))
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    return PPIResult(
        status=CORRECTED,
        estimate=estimate,
        raw_estimate=raw,
        label=CORRECTED,
        lower=estimate - z * se,
        upper=estimate + z * se,
        n_population=int(f.size),
        n_audit=int(n),
        effective_audit_n=n_eff,
    )
