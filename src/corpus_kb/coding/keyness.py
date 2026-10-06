"""Keyness statistics for distinctive term analysis.

Provides G2 log-likelihood (Dunning) and Monroe et al. 2008 informative-Dirichlet
log-odds z-score for measuring term distinctiveness across corpus partitions.
Callers pass term counts; this module stays pure and testable (no tokenization).
"""

from __future__ import annotations

import math


def g2(a: int, b: int, target_total: int, ref_total: int) -> tuple[float, float]:
    """G2 log-likelihood for one term: a=count in target, b=count in reference.

    Returns:
        tuple[float, float]: (signed_log_likelihood, log_ratio_effect_size)
        - signed_log_likelihood: positive if over-represented in target, negative otherwise
        - log_ratio: log2 of smoothed relative frequencies
    """
    if a == 0 and b == 0:
        return 0.0, 0.0
    n = target_total + ref_total
    e1 = target_total * (a + b) / n
    e2 = ref_total * (a + b) / n
    ll = 0.0
    if a > 0 and e1 > 0:
        ll += a * math.log(a / e1)
    if b > 0 and e2 > 0:
        ll += b * math.log(b / e2)
    ll *= 2
    # Log Ratio effect size (relative freq), with +0.5 smoothing on zero
    ta = a / target_total if target_total else 0
    rb = b / ref_total if ref_total else 0
    ta_s = (a + 0.5) / (target_total + 0.5)
    rb_s = (b + 0.5) / (ref_total + 0.5)
    log_ratio = math.log2(ta_s / rb_s) if rb_s > 0 else float("inf")
    over = ta > rb
    return (ll if over else -ll), log_ratio


def log_odds_dirichlet(
    a: int, b: int, target_total: int, ref_total: int, alpha: float = 0.01
) -> float:
    """Monroe et al. 2008 informative-Dirichlet log-odds z-score.

    Measures distinctiveness of a term across two corpus partitions using
    Dirichlet-smoothed log-odds with variance from the posterior.

    Args:
        a: count in target group
        b: count in reference group
        target_total: total term count in target group
        ref_total: total term count in reference group
        alpha: Dirichlet prior strength (default 0.01 for informative prior)

    Returns:
        float: z-score (log-odds / sqrt(variance))
    """
    # Dirichlet-smoothed counts
    a_smooth = a + alpha
    b_smooth = b + alpha
    not_a = target_total - a + alpha
    not_b = ref_total - b + alpha

    # Log-odds ratio: log(P(term|target) / P(not_term|target)) - log(P(term|ref) / P(not_term|ref))
    log_odds = math.log(a_smooth / not_a) - math.log(b_smooth / not_b)

    # Variance of log-odds (from Dirichlet posterior variance)
    # Using the approximation: Var = 1/a_smooth + 1/not_a + 1/b_smooth + 1/not_b
    variance = 1.0 / a_smooth + 1.0 / not_a + 1.0 / b_smooth + 1.0 / not_b

    # z-score
    if variance <= 0:
        return 0.0
    z_score = log_odds / math.sqrt(variance)
    return z_score
