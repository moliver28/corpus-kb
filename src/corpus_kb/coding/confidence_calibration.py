"""Confidence-signal evaluation + calibrator selection (U47; v8 §3 U47).

On CALIBRATION data: AUROC (rank-based, tie-aware), binned ECE, and Brier
score for each candidate uncertainty signal (logprob entropy, verbalized
0-100, cross-run agreement, cross-family agreement). A simple calibrator is
fitted locally in numpy (Platt scaling / logistic combination by full-batch
gradient descent; isotonic ONLY when scikit-learn is importable — checked at
call time, never at module import), and the chosen routing score is
evaluated ONCE on the TEST split. The chosen signal + calibrator come back
as a dataclass for the release packet.

RAW VERBALIZED SCORES ARE NEVER PROBABILITIES: an uncalibrated signal is
reported as measured, but routing uses only the calibrated output.

All signals are oriented so HIGHER = more likely correct before calling;
callers must pre-negate entropy-style signals (higher entropy = worse).
Pure numpy; deterministic (fixed-iteration gradient descent, no RNG).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

ECE_BINS = 10
PLATT_ITERS = 2000
PLATT_LR = 0.1
PLATT_L2 = 1e-3


@dataclass(frozen=True)
class SignalEvaluation:
    """Discrimination + calibration metrics for one candidate signal."""

    signal: str
    auroc: float | None  # None when the split has a single class
    ece: float
    brier: float
    n: int


@dataclass(frozen=True)
class Calibrator:
    """Fitted mapping signal(s) -> calibrated probability of correctness.

    ``kind`` is ``platt`` (one signal: p = sigmoid(a * x + b)),
    ``logistic_combination`` (k signals: p = sigmoid(w . x + b)),
    ``isotonic`` (step function from sklearn, ``isotonic_points`` set), or
    ``identity`` (no transform; used when nothing beat the raw signal).
    """

    kind: str
    weights: tuple[float, ...]
    bias: float
    isotonic_points: tuple[tuple[float, float], ...] = ()

    def predict(self, signals: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
        """Apply the fitted map; input is (n,) for platt/isotonic or (n, k) otherwise."""
        if self.kind == "isotonic":
            xs = [p[0] for p in self.isotonic_points]
            ys = [p[1] for p in self.isotonic_points]
            x = np.asarray(signals, dtype=float).ravel()
            return np.interp(x, xs, ys)
        x = np.asarray(signals, dtype=float)
        if x.ndim == 1:
            x = x[:, None]
        w = np.asarray(self.weights, dtype=float)
        logits = np.clip(x @ w + self.bias, -30.0, 30.0)
        return 1.0 / (1.0 + np.exp(-logits))


@dataclass(frozen=True)
class CalibrationChoice:
    """Release-packet record: which signal + calibrator routes units (U47)."""

    status: str  # ok | insufficient_data
    chosen_signal: str
    calibrator: Calibrator
    calibration_metrics: tuple[SignalEvaluation, ...]
    test_metrics: tuple[SignalEvaluation, ...] = field(default_factory=tuple)
    reason: str = ""


def auroc(scores: np.ndarray, outcomes: np.ndarray) -> float | None:
    """Tie-aware rank AUROC; None when one class is absent (undefined)."""
    s = np.asarray(scores, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=int).ravel()
    if s.size != y.size or s.size == 0:
        raise ValueError("scores and outcomes must align and be non-empty")
    n_pos = int(np.sum(y == 1))
    n_neg = int(np.sum(y == 0))
    if n_pos == 0 or n_neg == 0:
        return None
    order = np.argsort(s, kind="stable")
    ranks = np.empty(s.size, dtype=float)
    sorted_s = s[order]
    i = 0
    while i < s.size:
        j = i
        while j + 1 < s.size and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # average 1-based rank of the tie block
        ranks[order[i : j + 1]] = avg
        i = j + 1
    rank_sum_pos = float(np.sum(ranks[y == 1]))
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def expected_calibration_error(
    probs: np.ndarray, outcomes: np.ndarray, n_bins: int = ECE_BINS
) -> float:
    """Equal-width binned ECE over probabilities in [0, 1]."""
    p = np.asarray(probs, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=float).ravel()
    if p.size != y.size or p.size == 0:
        raise ValueError("probs and outcomes must align and be non-empty")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        mask = idx == b
        if not np.any(mask):
            continue
        conf = float(np.mean(p[mask]))
        acc = float(np.mean(y[mask]))
        ece += float(np.sum(mask)) / p.size * abs(acc - conf)
    return ece


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean squared error of probabilities against binary outcomes."""
    p = np.asarray(probs, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=float).ravel()
    if p.size != y.size or p.size == 0:
        raise ValueError("probs and outcomes must align and be non-empty")
    return float(np.mean((p - y) ** 2))


def evaluate_signal(signal: str, scores: np.ndarray, outcomes: np.ndarray) -> SignalEvaluation:
    """AUROC + ECE + Brier for one candidate signal (calibration split)."""
    s = np.asarray(scores, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=float).ravel()
    clipped = np.clip(s, 0.0, 1.0)
    return SignalEvaluation(
        signal=signal,
        auroc=auroc(s, y),
        ece=expected_calibration_error(clipped, y),
        brier=brier_score(clipped, y),
        n=int(s.size),
    )


def fit_logistic(
    features: np.ndarray, outcomes: np.ndarray, *, l2: float = PLATT_L2, iters: int = PLATT_ITERS
) -> Calibrator:
    """Local logistic (Platt when features is one column) via full-batch GD.

    Deterministic: fixed iteration count, zero init, no RNG. L2 keeps the
    weights finite on separable calibration data.
    """
    x = np.asarray(features, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    y = np.asarray(outcomes, dtype=float).ravel()
    if x.shape[0] != y.size or x.shape[0] == 0:
        raise ValueError("features and outcomes must align and be non-empty")
    n, k = x.shape
    w = np.zeros(k, dtype=float)
    b = 0.0
    for _ in range(iters):
        logits = np.clip(x @ w + b, -30.0, 30.0)
        p = 1.0 / (1.0 + np.exp(-logits))
        grad_w = x.T @ (p - y) / n + l2 * w
        grad_b = float(np.mean(p - y))
        w = w - PLATT_LR * grad_w
        b = b - PLATT_LR * grad_b
    kind = "platt" if k == 1 else "logistic_combination"
    return Calibrator(kind=kind, weights=tuple(float(v) for v in w), bias=b)


class _IsotonicModel(Protocol):
    """Structural type of a fitted sklearn IsotonicRegression instance."""

    X_thresholds_: np.ndarray
    y_thresholds_: np.ndarray

    def fit(self, x: np.ndarray, y: np.ndarray) -> object: ...


class _IsotonicFactory(Protocol):
    """Structural type of the sklearn IsotonicRegression class itself."""

    def __call__(self, *, y_min: float, y_max: float, out_of_bounds: str) -> _IsotonicModel: ...


def _fit_isotonic(
    sklearn_isotonic: _IsotonicFactory | None, scores: np.ndarray, outcomes: np.ndarray
) -> Calibrator | None:
    """Fit sklearn isotonic regression behind an injected factory (or None).

    ``sklearn_isotonic`` is resolved by the caller inside a try/ImportError;
    None keeps the sklearn-free path. The fitted step function is stored as
    (x, y) points on a plain Calibrator (no sklearn types leak out).
    """
    if sklearn_isotonic is None:
        return None
    iso = sklearn_isotonic(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    x = np.asarray(scores, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=float).ravel()
    iso.fit(x, y)
    thresholds = [float(v) for v in iso.X_thresholds_]
    values = [float(v) for v in iso.y_thresholds_]
    return Calibrator(
        kind="isotonic",
        weights=tuple(values),
        bias=0.0,
        isotonic_points=tuple(zip(thresholds, values, strict=True)),
    )


def choose_routing_signal(
    calibration_signals: dict[str, Sequence[float]],
    calibration_outcomes: Sequence[float],
    test_signals: dict[str, Sequence[float]],
    test_outcomes: Sequence[float],
    *,
    prefer_isotonic: bool = False,
) -> CalibrationChoice:
    """Pick the routing score: best calibrated signal on CALIBRATION data.

    Every candidate is evaluated on the calibration split (AUROC first, then
    lower ECE as tie-break); the best single signal gets a Platt calibrator
    (isotonic only when ``prefer_isotonic`` and sklearn is importable). The
    winner is evaluated ONCE on the test split. With fewer than 2 outcomes
    per class the honest ``insufficient_data`` status is returned — no
    routing score is fabricated.
    """
    y_cal = np.asarray(calibration_outcomes, dtype=float).ravel()
    if y_cal.size == 0 or len({float(v) for v in y_cal}) < 2:
        return CalibrationChoice(
            status="insufficient_data",
            chosen_signal="",
            calibrator=Calibrator(kind="identity", weights=(0.0,), bias=0.0),
            calibration_metrics=(),
            test_metrics=(),
            reason="calibration outcomes need both classes present",
        )
    metrics = [
        evaluate_signal(name, np.asarray(vals, dtype=float), y_cal)
        for name, vals in calibration_signals.items()
    ]
    scored = [(m, i) for i, m in enumerate(metrics) if m.auroc is not None]
    if not scored:
        return CalibrationChoice(
            status="insufficient_data",
            chosen_signal="",
            calibrator=Calibrator(kind="identity", weights=(0.0,), bias=0.0),
            calibration_metrics=tuple(metrics),
            test_metrics=(),
            reason="no candidate signal discriminates both classes",
        )
    best_metric, best_idx = max(
        scored, key=lambda t: (t[0].auroc if t[0].auroc is not None else 0.0, -t[0].ece)
    )
    best_name = list(calibration_signals)[best_idx]
    best_scores = np.asarray(calibration_signals[best_name], dtype=float)
    iso_cls = _import_isotonic() if prefer_isotonic else None
    calibrator: Calibrator | None = None
    if prefer_isotonic:
        calibrator = _fit_isotonic(iso_cls, best_scores, y_cal)
    if calibrator is None:
        calibrator = fit_logistic(best_scores, y_cal)
    calibrated_test = calibrator.predict(np.asarray(test_signals[best_name], dtype=float))
    test_eval = evaluate_signal(
        f"{best_name}[{calibrator.kind}]", calibrated_test, np.asarray(test_outcomes, dtype=float)
    )
    return CalibrationChoice(
        status="ok",
        chosen_signal=best_name,
        calibrator=calibrator,
        calibration_metrics=tuple(metrics),
        test_metrics=(test_eval,),
        reason=(
            f"chosen {best_name} (calibration AUROC={best_metric.auroc:.3f}, "
            f"ECE={best_metric.ece:.3f}); calibrated with {calibrator.kind}; "
            f"test evaluated once (AUROC={test_eval.auroc}, Brier={test_eval.brier:.3f})"
        ),
    )


def _import_isotonic() -> _IsotonicFactory | None:
    """Resolve sklearn's IsotonicRegression at CALL time (optional extra).

    Dynamic importlib resolution: scikit-learn is an optional extra and must
    never be a module-level import; a missing install simply keeps the
    local logistic path.
    """
    import importlib

    try:
        module = importlib.import_module("sklearn.isotonic")
    except ImportError:
        return None
    factory: _IsotonicFactory | None = getattr(module, "IsotonicRegression", None)
    return factory
