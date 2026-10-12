"""Alternative Annotator Test (U13; v6 §7 U13).

Implemented from the primary paper: Nitay Calderon, Roi Reichart, and
Rotem Dror. 2025. "The Alternative Annotator Test for LLM-as-a-Judge: How
to Statistically Justify Replacing Human Annotators with LLMs." ACL 2025
(arXiv:2501.10970). Method, verbatim shape:

1. Leave one human annotator h_j out at a time. For each instance x_i that
   h_j annotated, compute the alignment score of the LLM against the
   REMAINING annotators, S(f, x_i, j) = mean_k 1{f(x_i) = h_k(x_i)} over
   k in H_i \\ {j} (ACC form for discrete labels), and the same score with
   h_j in the LLM seat, S(h_j, x_i, j).
2. Advantage probabilities: rho_j^f = mean_i 1{S(f, x_i, j) >= S(h_j, x_i, j)}
   and rho_j^h with the reversed comparison. Ties count for BOTH sides.
3. One-sided test per annotator: H0: rho_j^f <= rho_j^h - epsilon vs
   H1: rho_j^f > rho_j^h - epsilon, on the paired differences
   d_i = W^h_i - W^f_i in {-1, 0, 1}. The paper uses a paired t-test
   (recommending a non-parametric test for n < 30); this module offers
   ``exact_binomial`` (exact one-sided lower-tail binomial on the human's
   strict wins with the margin folded into the null probability —
   numpy/stdlib only, valid at any n) and ``bootstrap`` (non-parametric
   H0-projected bootstrap).
4. Benjamini-Yekutieli FDR correction across the m annotator p-values
   (the paper's choice; the tests are dependent through the shared
   leave-one-out raters), q = 0.05.
5. Winning rate omega = fraction of rejected nulls; the LLM passes when
   omega >= 0.5 (configurable). The Average Advantage Probability
   rho = mean_j rho_j^f is reported alongside as the paper's comparison
   measure.

Requires multiply-annotated units (each unit >= 2 human annotators so a
leave-one-out score exists) and >= 3 annotators overall; otherwise
``insufficient_data``. Pure numpy/stdlib; the bootstrap is the only
stochastic part and takes an explicit seed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

PASS = "pass"
FAIL = "fail"
INSUFFICIENT_DATA = "insufficient_data"

METHOD_EXACT_BINOMIAL = "exact_binomial"
METHOD_BOOTSTRAP = "bootstrap"


@dataclass(frozen=True)
class AnnotatorVerdict:
    """Per-annotator alt-test components (paper §3.2-3.3)."""

    annotator: str
    rho_f: float  # P(LLM >= h_j) estimated from the paired indicators
    rho_h: float  # P(h_j >= LLM)
    n_instances: int
    p_value: float
    rejected: bool


@dataclass(frozen=True)
class AltTestResult:
    """Gate-ready alt-test outcome (U13)."""

    status: str  # pass | fail | insufficient_data
    winning_rate: float | None  # omega
    advantage_probability: float | None  # rho = mean_j rho_j^f
    n_annotators: int
    n_instances: int
    verdicts: tuple[AnnotatorVerdict, ...]
    reason: str


def _alignment_scores(
    annotations: Mapping[str, Mapping[str, str]],
    model_preds: Mapping[str, str],
) -> tuple[dict[str, list[tuple[float, float]]], int]:
    """Per-annotator lists of paired (S(f), S(h_j)) scores over shared units.

    Returns (per-annotator score pairs keyed by annotator id, count of
    multiply-annotated units used). Units with fewer than 2 human annotators
    contribute nothing (no leave-one-out remainder exists).
    """
    pairs: dict[str, list[tuple[float, float]]] = {}
    n_units_used = 0
    for unit, raters in annotations.items():
        human_ids = sorted(raters)
        if len(human_ids) < 2:
            continue
        n_units_used += 1
        for held_out in human_ids:
            others = [h for h in human_ids if h != held_out]
            model_label = model_preds.get(unit)
            s_model = (
                float(np.mean([raters[h] == model_label for h in others]))
                if model_label is not None
                else 0.0
            )
            s_human = float(np.mean([raters[h] == raters[held_out] for h in others]))
            pairs.setdefault(held_out, []).append((s_model, s_human))
    return pairs, n_units_used


def _exact_binomial_lower_tail(k: int, n: int, p: float) -> float:
    """Exact P(X <= k) for X ~ Binom(n, p), log-space summation (no scipy)."""
    if n <= 0 or k < 0:
        return 1.0 if k >= 0 or n <= 0 else 0.0
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


def _bootstrap_pvalue(diffs: np.ndarray, margin: float, n_bootstrap: int, seed: int) -> float:
    """One-sided bootstrap p-value for H0: mean(d) >= margin.

    Resamples are drawn from the H0-projected differences d - mean(d) +
    margin (centered at the null boundary); the p-value is the fraction of
    resample means at most as large as the observed mean (plus-one
    correction so p is never exactly 0). An observed mean far BELOW the
    margin yields a small p-value -> H0 rejected -> the LLM wins.
    """
    n = len(diffs)
    if n == 0:
        return 1.0
    rng = np.random.default_rng(seed)
    projected = diffs - diffs.mean() + margin
    observed = float(diffs.mean())
    exceed = 0
    for _ in range(n_bootstrap):
        sample = projected[rng.integers(0, n, size=n)]
        if sample.mean() <= observed:
            exceed += 1
    return (exceed + 1) / (n_bootstrap + 1)


def _benjamini_yekutieli(pvalues: list[float], q: float) -> list[bool]:
    """BY FDR step-up (Benjamini & Yekutieli 2001) for dependent tests.

    Rejects ranks 1..k where k is the largest index with
    p_(k) <= q * k / (m * c(m)), c(m) = sum_{i=1}^{m} 1/i.
    """
    m = len(pvalues)
    if m == 0:
        return []
    c_m = sum(1.0 / (i + 1) for i in range(m))
    order = sorted(range(m), key=lambda i: pvalues[i])
    rejected = [False] * m
    for rank in range(m, 0, -1):
        if pvalues[order[rank - 1]] <= q * rank / (m * c_m):
            for r in range(rank):
                rejected[order[r]] = True
            break
    return rejected


def alt_test(
    annotations: Mapping[str, Mapping[str, str]],
    model_preds: Mapping[str, str],
    *,
    margin: float = 0.1,
    fdr_q: float = 0.05,
    passing_fraction: float = 0.5,
    method: str = METHOD_EXACT_BINOMIAL,
    n_bootstrap: int = 10000,
    seed: int = 0,
) -> AltTestResult:
    """Run the Alternative Annotator Test (U13).

    Args:
        annotations: unit_id -> {annotator_id -> discrete label}. Units need
            >= 2 annotators (a leave-one-out remainder must exist).
        model_preds: unit_id -> model label. Units missing a model prediction
            score S(f) = 0.0 for every comparison (the model cannot align
            with anyone there) rather than silently dropping the unit.
        margin: Cost-benefit penalty epsilon (paper §3.3: larger = harder).
        fdr_q: Target false-discovery rate for the BY correction.
        passing_fraction: Required fraction of annotators beaten (paper: 0.5).
        method: ``exact_binomial`` (default, exact) or ``bootstrap``.
        n_bootstrap: Resamples for the bootstrap method.
        seed: Bootstrap seed (ignored by the exact method; deterministic).

    Returns:
        AltTestResult with status pass/fail, winning rate, advantage
        probability, and per-annotator verdicts. Fewer than 3 annotators or
        no multiply-annotated unit -> ``insufficient_data`` (never a pass).
    """
    if method not in (METHOD_EXACT_BINOMIAL, METHOD_BOOTSTRAP):
        raise ValueError(f"unknown alt-test method: {method!r}")
    score_pairs, n_units = _alignment_scores(annotations, model_preds)
    annotators = sorted(score_pairs)
    if len(annotators) < 3 or n_units == 0:
        return AltTestResult(
            status=INSUFFICIENT_DATA,
            winning_rate=None,
            advantage_probability=None,
            n_annotators=len(annotators),
            n_instances=n_units,
            verdicts=(),
            reason=(
                f"alt-test needs >= 3 annotators and >= 1 multiply-annotated "
                f"unit; got {len(annotators)} annotators, {n_units} usable units"
            ),
        )

    pvalues: list[float] = []
    rhos_f: list[float] = []
    verdicts: list[AnnotatorVerdict] = []
    for annotator in annotators:
        s_pairs = score_pairs[annotator]
        s_f = np.array([p[0] for p in s_pairs], dtype=float)
        s_h = np.array([p[1] for p in s_pairs], dtype=float)
        w_f = (s_f >= s_h).astype(float)  # ties count for both sides
        w_h = (s_h >= s_f).astype(float)
        rho_f = float(np.mean(w_f))
        rho_h = float(np.mean(w_h))
        diffs = w_h - w_f  # in {-1, 0, 1}; +1 = human strictly better
        n = len(diffs)
        wins_h = int(np.sum(diffs > 0))
        losses = int(np.sum(diffs < 0))
        n_trials = wins_h + losses
        if method == METHOD_BOOTSTRAP:
            p = _bootstrap_pvalue(diffs, margin, n_bootstrap, seed)
        elif n_trials == 0:
            p = 1.0  # pure ties: no evidence either way, never reject
        else:
            # H0 boundary: mean(d) = eps  <=>  wins_h ~ Binom(n', p0) with
            # (2*p0 - 1) * n' / n = eps  =>  p0 = 0.5 + eps * n / (2 * n').
            # Reject H0 (LLM wins) when wins_h is far in the LOWER tail.
            p0 = min(1.0, 0.5 + (margin * n) / (2.0 * n_trials))
            p = _exact_binomial_lower_tail(wins_h, n_trials, p0)
        pvalues.append(float(p))
        rhos_f.append(rho_f)
        verdicts.append(
            AnnotatorVerdict(
                annotator=annotator,
                rho_f=rho_f,
                rho_h=rho_h,
                n_instances=n,
                p_value=float(p),
                rejected=False,
            )
        )

    rejected_mask = _benjamini_yekutieli(pvalues, fdr_q)
    verdicts = [
        AnnotatorVerdict(
            annotator=v.annotator,
            rho_f=v.rho_f,
            rho_h=v.rho_h,
            n_instances=v.n_instances,
            p_value=v.p_value,
            rejected=bool(rejected_mask[i]),
        )
        for i, v in enumerate(verdicts)
    ]
    winning_rate = sum(1 for v in verdicts if v.rejected) / len(verdicts)
    advantage_probability = float(np.mean(rhos_f))
    passed = winning_rate >= passing_fraction
    return AltTestResult(
        status=PASS if passed else FAIL,
        winning_rate=winning_rate,
        advantage_probability=advantage_probability,
        n_annotators=len(verdicts),
        n_instances=n_units,
        verdicts=tuple(verdicts),
        reason=(
            f"winning_rate={winning_rate:.3f} vs required {passing_fraction} "
            f"(BY FDR q={fdr_q}, margin={margin}, method={method})"
        ),
    )
