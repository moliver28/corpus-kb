"""Exact conjugate belief math (Living Corpus W4).

A belief is a Beta posterior over "the subject's stance toward the
object is positive". Evidence items carry a weight (extraction
confidence x template validity x conflict factor) and an observed_at;
the power-prior forgetting factor exp(-age/half_life) discounts old
evidence, so the current belief tracks the present while every
historical item stays in the log (recompute-on-read: exact, O(n), no
drift). The tenant-level stance ratio shrinks sparse beliefs toward the
tenant prior (empirical-Bayes partial pooling). No sampling anywhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_HALF_LIFE_DAYS = 365.0
POOLING_STRENGTH = 4.0


@dataclass(frozen=True)
class Evidence:
    """One discounted evidence item (a statement's stance)."""

    stance: str  # "positive" | "negative"
    weight: float
    observed_at: datetime


@dataclass(frozen=True)
class Posterior:
    """The exact posterior summary reported on every belief op."""

    alpha: float
    beta: float
    mean: float
    interval_low: float
    interval_high: float
    evidence_count: int
    era_count: int


def discount(age_days: float, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    """Exponential forgetting: 1.0 now, 0.5 one half-life ago."""
    return math.pow(0.5, max(age_days, 0.0) / half_life_days) if half_life_days > 0 else 1.0


def tenant_prior(tenant_positive_ratio: float | None) -> tuple[float, float]:
    """Empirical-Bayes prior: the tenant ratio, pooled over POOLING_STRENGTH."""
    ratio = (
        tenant_positive_ratio
        if (tenant_positive_ratio is not None and 0.0 <= tenant_positive_ratio <= 1.0)
        else 0.5
    )
    return (1.0 + POOLING_STRENGTH * ratio, 1.0 + POOLING_STRENGTH * (1.0 - ratio))


def posterior(
    evidence: list[Evidence],
    *,
    now: datetime | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    tenant_positive_ratio: float | None = None,
    era_count: int = 1,
) -> Posterior:
    """Exact sequential conjugate update with power-prior forgetting."""
    now = now or datetime.now(UTC)
    alpha0, beta0 = tenant_prior(tenant_positive_ratio)
    alpha, beta = alpha0, beta0
    for item in evidence:
        age_days = max((now - item.observed_at).total_seconds() / 86400.0, 0.0)
        weighted = max(item.weight, 0.0) * discount(age_days, half_life_days)
        if item.stance == "positive":
            alpha += weighted
        else:
            beta += weighted
    total = alpha + beta
    mean = alpha / total if total > 0 else 0.5
    low, high = beta_interval(alpha, beta)
    return Posterior(
        alpha=round(alpha, 6),
        beta=round(beta, 6),
        mean=round(mean, 6),
        interval_low=low,
        interval_high=high,
        evidence_count=len(evidence),
        era_count=era_count,
    )


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (Numerical Recipes)."""
    max_iterations = 200
    epsilon = 3.0e-9
    tiny = 1.0e-30
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) (symmetric continued fraction)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    front = math.exp(ln_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def beta_interval(
    alpha: float, beta: float, *, lower: float = 0.025, upper: float = 0.975
) -> tuple[float, float]:
    """Equal-tail credible interval by bisection on the incomplete beta."""

    def quantile(q: float) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if betainc(alpha, beta, mid) < q:
                lo = mid
            else:
                hi = mid
        return round((lo + hi) / 2.0, 6)

    return quantile(lower), quantile(upper)
