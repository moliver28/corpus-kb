"""Dawid-Skene EM aggregation + weak supervision (U33, U38; v6 §7 U33, v8 §3 U38).

U33: one-shot EM over per-annotator confusion matrices (the ~50-line numpy
form: majority-vote init, E-step posterior over classes, M-step refit of
priors and per-annotator error rates). Produces adjudicated reference labels
PLUS posterior uncertainty, feeding U11 (held-out P/R/F1 against the
reference) and U14 (PPI residuals against the reference).

ASSUMPTION (documented, binding): conditional independence of annotator
errors given the true class. Same-family machine coders sharing a base model
VIOLATE it (correlated errors make the EM overcount evidence); guarding the
panel is U34's job (proposer/validator from different model families), not
this module's. Callers must pass the U34 guard before trusting EM output
over a mixed panel.

U38: weak supervision over >= ``min_signals`` signals via the EM above;
below the floor -> ``insufficient_signals`` with majority/weighted vote
fallback, ties -> review. No Snorkel dependency. Abstain is expressible per
signal (missing entry) and per item (low posterior confidence).

All functions are pure numpy, deterministic (majority-vote init; ties break
to the lowest class index; no RNG anywhere).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MISSING = -1  # sentinel for an annotator/signal that abstained on an item


@dataclass(frozen=True)
class DawidSkeneResult:
    """Adjudicated reference labels with posterior uncertainty (U33)."""

    labels: np.ndarray  # (n_items,) MAP class index per item
    posterior: np.ndarray  # (n_items, n_classes) posterior over classes
    class_priors: np.ndarray  # (n_classes,)
    error_rates: np.ndarray  # (n_annotators, n_classes, n_classes) confusion
    n_iter: int
    converged: bool
    n_observed: int  # total observed (annotator, item) ratings used


def _majority_vote_init(annotations: np.ndarray, n_classes: int) -> np.ndarray:
    """Per-item majority vote; ties break to the lowest class index."""
    n_items = annotations.shape[0]
    hard = np.zeros(n_items, dtype=int)
    for i in range(n_items):
        observed = annotations[i][annotations[i] != MISSING]
        if len(observed) == 0:
            hard[i] = 0
            continue
        counts = np.bincount(observed, minlength=n_classes).astype(float)
        hard[i] = int(np.argmax(counts))  # argmax returns first max -> lowest index
    return hard


def dawid_skene(
    annotations: np.ndarray,
    n_classes: int,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> DawidSkeneResult:
    """Fit the Dawid-Skene model by EM.

    Args:
        annotations: (n_items, n_annotators) int matrix of class labels;
            ``MISSING`` (-1) marks an annotator's abstention on an item.
        n_classes: Number of label classes (0..n_classes-1).
        max_iter: EM iteration cap.
        tol: Convergence tolerance on the mean posterior shift.

    Returns:
        DawidSkeneResult with MAP labels, posteriors, priors, and per-annotator
        confusion matrices (error_rates[j] maps true class -> annotated class).
    """
    a = np.asarray(annotations, dtype=int)
    if a.ndim != 2:
        raise ValueError("annotations must be (n_items, n_annotators)")
    n_items, n_annotators = a.shape
    if n_items == 0 or n_annotators == 0:
        raise ValueError("annotations must be non-empty")
    if (a[a != MISSING] >= n_classes).any() or (a[a != MISSING] < 0).any():
        raise ValueError("labels must be in [0, n_classes) or MISSING")

    observed = a != MISSING
    # Init: one-hot majority vote (deterministic; ties -> lowest class).
    init = _majority_vote_init(a, n_classes)
    posterior = np.zeros((n_items, n_classes), dtype=float)
    posterior[np.arange(n_items), init] = 1.0
    error = np.empty((n_annotators, n_classes, n_classes), dtype=float)
    prior = np.full(n_classes, 1.0 / n_classes)
    n_iter = 0
    converged = False
    for step in range(1, max_iter + 1):
        n_iter = step
        # M-step: priors + per-annotator confusion from the current posteriors.
        prior = posterior.mean(axis=0)
        for j in range(n_annotators):
            error[j] = np.full((n_classes, n_classes), 1e-8)
            idx = np.where(observed[:, j])[0]
            for i in idx:
                error[j][:, a[i, j]] += posterior[i]
            error[j] /= error[j].sum(axis=1, keepdims=True)
        # E-step: posterior(c) proportional to prior_c * prod_j T_j[c, a_ij].
        log_post = np.log(np.clip(prior, 1e-12, None))[None, :].repeat(n_items, axis=0)
        for j in range(n_annotators):
            idx = observed[:, j]
            log_post[idx] += np.log(np.clip(error[j][:, a[idx, j]].T, 1e-12, None))
        new_posterior = np.exp(log_post - log_post.max(axis=1, keepdims=True))
        new_posterior /= new_posterior.sum(axis=1, keepdims=True)
        shift = float(np.mean(np.abs(new_posterior - posterior)))
        posterior = new_posterior
        if shift < tol:
            converged = True
            break
    hard = np.argmax(posterior, axis=1)
    return DawidSkeneResult(
        labels=hard,
        posterior=posterior,
        class_priors=prior,
        error_rates=error,
        n_iter=n_iter,
        converged=converged,
        n_observed=int(observed.sum()),
    )


@dataclass(frozen=True)
class WeakSupervisionResult:
    """U38 weak-supervision aggregation outcome.

    ``status`` is ``ok`` (>= min_signals, aggregation applied) or
    ``insufficient_signals`` (majority-vote fallback active). ``review`` is
    the tie/low-confidence mask routing items to human review; ``abstain``
    marks items the aggregation declines to label (strict superset
    semantics: a tie always routes to review even under vote fallback).
    """

    status: str
    labels: np.ndarray  # (n_items,) MAP label, undefined where abstain
    probabilities: np.ndarray  # (n_items, n_classes)
    signal_accuracy: np.ndarray | None  # (n_signals,) EM-estimated accuracy
    abstain: np.ndarray  # (n_items,) bool: aggregation declines
    review: np.ndarray  # (n_items,) bool: route to human review
    detail: str


def aggregate_signals(
    signals: np.ndarray,
    n_classes: int,
    *,
    min_signals: int = 3,
    method: str = "em",
    abstain_threshold: float = 0.6,
    max_iter: int = 100,
) -> WeakSupervisionResult:
    """Aggregate >= min_signals independent-ish labeling signals (U38).

    Args:
        signals: (n_items, n_signals) int matrix; ``MISSING`` = signal
            abstained on that item.
        n_classes: Label class count.
        min_signals: Minimum distinct signals for EM (v8 §3 U38 default 3).
        method: ``em`` (Dawid-Skene; learns per-signal accuracy) or ``vote``
            (majority vote; tie -> review).
        abstain_threshold: Items whose winning posterior falls below this
            are marked abstain (and routed to review under EM).
        max_iter: EM iteration cap (passed through to dawid_skene).

    Returns:
        WeakSupervisionResult. With fewer than ``min_signals`` distinct
        signals the status is ``insufficient_signals`` and the vote fallback
        is used regardless of ``method`` — never a fabricated EM.
    """
    s = np.asarray(signals, dtype=int)
    if s.ndim != 2:
        raise ValueError("signals must be (n_items, n_signals)")
    n_items, n_signals = s.shape
    if n_items == 0:
        raise ValueError("signals must be non-empty")
    if (s[s != MISSING] >= n_classes).any() or (s[s != MISSING] < 0).any():
        raise ValueError("signal labels must be in [0, n_classes) or MISSING")

    if n_signals < min_signals or method == "vote":
        return _vote_fallback(s, n_classes, n_signals, min_signals, method)

    ds = dawid_skene(s, n_classes, max_iter=max_iter)
    accuracy = np.array([float(np.trace(ds.error_rates[j])) / n_classes for j in range(n_signals)])
    confidence = ds.posterior.max(axis=1)
    abstain = confidence < abstain_threshold
    review = abstain.copy()
    if n_classes > 1:
        # Ties in the posterior (two equal maxima) also route to review.
        top = np.sort(ds.posterior, axis=1)
        review = review | np.isclose(top[:, -1], top[:, -2])
    detail = (
        f"dawid_skene em over {n_signals} signals; converged={ds.converged} after {ds.n_iter} iters"
    )
    return WeakSupervisionResult(
        status="ok",
        labels=ds.labels,
        probabilities=ds.posterior,
        signal_accuracy=accuracy,
        abstain=abstain,
        review=review,
        detail=detail,
    )


def _vote_fallback(
    s: np.ndarray,
    n_classes: int,
    n_signals: int,
    min_signals: int,
    method: str,
) -> WeakSupervisionResult:
    """Majority vote with tie -> review; honest ``insufficient_signals`` floor."""
    n_items = s.shape[0]
    labels = np.zeros(n_items, dtype=int)
    counts = np.zeros((n_items, n_classes), dtype=float)
    abstain = np.zeros(n_items, dtype=bool)
    review = np.zeros(n_items, dtype=bool)
    for i in range(n_items):
        observed = s[i][s[i] != MISSING]
        if len(observed) == 0:
            abstain[i] = True
            review[i] = True
            continue
        c = np.bincount(observed, minlength=n_classes).astype(float)
        counts[i] = c
        best = int(np.argmax(c))
        labels[i] = best
        # Tie: more than one class shares the top count -> human review.
        if int((c == c[best]).sum()) > 1:
            review[i] = True
    status = "insufficient_signals" if n_signals < min_signals else "ok"
    detail = (
        f"majority-vote fallback ({method} requested, {n_signals} signals, "
        f"min_signals={min_signals}); ties route to review"
    )
    prior = np.full(n_classes, 1.0 / max(n_classes, 1))
    probs = prior[None, :].repeat(n_items, axis=0)
    totals = counts.sum(axis=1, keepdims=True)
    observed_any = totals[:, 0] > 0
    probs[observed_any] = counts[observed_any] / totals[observed_any]
    return WeakSupervisionResult(
        status=status,
        labels=labels,
        probabilities=probs,
        signal_accuracy=None,
        abstain=abstain,
        review=review,
        detail=detail,
    )


def evaluate_agreement(
    predicted: np.ndarray, heldout_truth: np.ndarray, mask: np.ndarray | None = None
) -> dict[str, float | int]:
    """Held-out agreement of aggregated labels vs human truth (pre-use check).

    v8 §3 U38: "Evaluate against held-out human labels before use." Returns
    accuracy plus the reviewed-item count; callers gate deployment on it.
    """
    p = np.asarray(predicted, dtype=int)
    t = np.asarray(heldout_truth, dtype=int)
    if p.shape != t.shape:
        raise ValueError("predicted and heldout_truth must align")
    keep = np.ones(len(t), dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
    n = int(keep.sum())
    if n == 0:
        return {"n": 0, "accuracy": 0.0}
    return {"n": n, "accuracy": float(np.mean(p[keep] == t[keep]))}
