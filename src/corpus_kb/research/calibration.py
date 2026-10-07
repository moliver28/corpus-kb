"""Threshold calibration, temperature scaling, and conformal prediction (todo 14).

All statistics are numpy-only. Thresholds are fitted by stratified k-fold CV;
prototypes are rebuilt inside each training fold so a held-out exemplar is
never scored against a medoid chosen from it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np

from corpus_kb.research.prototypes import build_view_prototypes
from corpus_kb.research.scoring import score_code_batch

N_FOLDS = 5
TEMPERATURE_GRID = np.linspace(0.1, 5.0, 50)
CONFORMAL_ALPHA = 0.1
ECE_BINS = 10


@dataclass(frozen=True)
class Thresholds:
    """Calibrated per-code decision thresholds."""

    tau_a: float
    tau_qa: float
    delta: float
    m_gz_a: float
    m_gz_qa: float
    m_gz_delta: float
    cv_range: dict[str, object]
    n_gold: int
    unreliable: bool


def _f1_at_threshold(scores: np.ndarray, labels: np.ndarray, threshold: float) -> float:
    pred = scores >= threshold
    tp = int(np.sum((pred == 1) & (labels == 1)))
    fp = int(np.sum((pred == 1) & (labels == 0)))
    fn = int(np.sum((pred == 0) & (labels == 1)))
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return 2 * precision * recall / (precision + recall)


def _fit_binary_threshold(scores: np.ndarray, labels: np.ndarray) -> float:
    """Pick threshold maximizing F1 over candidate unique scores."""
    if len(labels) == 0:
        return 0.0
    positives = scores[labels == 1]
    if len(positives) == 0:
        return 0.0
    candidates = np.unique(np.concatenate([[0.0], positives, scores]))
    best_t, best_f1 = 0.0, 0.0
    for t in candidates:
        f1 = _f1_at_threshold(scores, labels, float(t))
        if f1 > best_f1:
            best_f1 = f1
            best_t = float(t)
    return best_t


def _fit_delta_threshold(gain: np.ndarray, qdep_labels: np.ndarray) -> float:
    """Pick delta maximizing F1 of question-dependent assignments."""
    if len(qdep_labels) == 0 or np.sum(qdep_labels) == 0:
        return 0.0
    candidates = np.unique(np.concatenate([[0.0], gain[qdep_labels == 1], gain]))
    best_d, best_f1 = 0.0, 0.0
    for d in candidates:
        f1 = _f1_at_threshold(gain, qdep_labels, float(d))
        if f1 > best_f1:
            best_f1 = f1
            best_d = float(d)
    return max(0.0, best_d)


def _stratified_folds(
    n: int, labels: np.ndarray, n_folds: int, rng: np.random.Generator
) -> list[np.ndarray]:
    """Distribute positives and negatives round-robin across folds.

    Guarantees each fold contains representation of the positive class.
    """
    pos = np.where(labels == 1)[0]
    neg = np.where(labels == 0)[0]
    rng.shuffle(pos)
    rng.shuffle(neg)
    folds: list[list[int]] = [[] for _ in range(n_folds)]
    for i, idx in enumerate(pos):
        folds[i % n_folds].append(int(idx))
    for i, idx in enumerate(neg):
        folds[i % n_folds].append(int(idx))
    return [np.array(f, dtype=int) for f in folds]


def _median_or_zero(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(np.median(values))


def _std_or_zero(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(np.std(values, ddof=1))


def _range_dict(values: dict[str, Sequence[float]]) -> dict[str, object]:
    return {
        name: {"min": float(np.min(v)) if v else 0.0, "max": float(np.max(v)) if v else 0.0}
        for name, v in values.items()
    }


def fit_code_thresholds(
    gold_units: list[dict[str, object]],
    code_id: str,
    n_folds: int = N_FOLDS,
    rng: np.random.Generator | None = None,
) -> Thresholds:
    """Fit tau_a / tau_qa / delta for one code via stratified k-fold CV.

    ``gold_units`` are dicts with keys:
      - ``code_id`` (str)
      - ``label`` (1 if positive for this code, else 0)
      - ``evidence_basis`` (``'explicit_in_answer'`` or ``'question_dependent'``)
      - ``answer``, ``qa``, ``question`` vectors (list[float])

    Prototypes are rebuilt inside each training fold.
    """
    rng = rng or np.random.default_rng(0)
    relevant = [u for u in gold_units if str(u.get("code_id", "")) == code_id]
    if not relevant:
        return Thresholds(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, {}, 0, True)
    labels = np.array([int(cast(int, u["label"])) for u in relevant], dtype=int)
    n_pos = int(np.sum(labels))
    if n_pos < 2:
        return Thresholds(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, {}, len(relevant), True)

    folds = _stratified_folds(len(relevant), labels, min(n_folds, n_pos), rng)

    fold_tau_a: list[float] = []
    fold_tau_qa: list[float] = []
    fold_delta: list[float] = []

    for fold_idx in range(len(folds)):
        test = folds[fold_idx]
        train = np.concatenate([folds[i] for i in range(len(folds)) if i != fold_idx])
        train_units = [relevant[i] for i in train]
        test_units = [relevant[i] for i in test]

        pos_train = [u for u in train_units if int(cast(int, u["label"])) == 1]
        if not pos_train:
            continue
        answer_protos = build_view_prototypes(
            [cast(Sequence[float], u["answer"]) for u in pos_train], rng=rng
        )
        qa_protos = build_view_prototypes(
            [cast(Sequence[float], u["qa"]) for u in pos_train], rng=rng
        )
        question_protos = build_view_prototypes(
            [cast(Sequence[float], u["question"]) for u in pos_train], rng=rng
        )
        prototypes = {"answer": answer_protos, "qa": qa_protos, "question": question_protos}
        scores = score_code_batch(
            [cast(Sequence[float], u["answer"]) for u in test_units],
            [cast(Sequence[float], u["qa"]) for u in test_units],
            [cast(Sequence[float], u["question"]) for u in test_units],
            prototypes,
        )
        test_labels = np.array([int(cast(int, u["label"])) for u in test_units], dtype=int)
        qdep = np.array(
            [1 if str(u.get("evidence_basis")) == "question_dependent" else 0 for u in test_units],
            dtype=int,
        )
        gain = scores.qa - scores.question
        fold_tau_a.append(_fit_binary_threshold(scores.answer, test_labels))
        fold_tau_qa.append(_fit_binary_threshold(scores.qa, test_labels))
        fold_delta.append(_fit_delta_threshold(gain, qdep))

    tau_a = _median_or_zero(fold_tau_a)
    tau_qa = _median_or_zero(fold_tau_qa)
    delta = _median_or_zero(fold_delta)
    m_gz_a = _std_or_zero(fold_tau_a)
    m_gz_qa = _std_or_zero(fold_tau_qa)
    m_gz_delta = _std_or_zero(fold_delta)

    return Thresholds(
        tau_a=tau_a,
        tau_qa=tau_qa,
        delta=delta,
        m_gz_a=m_gz_a,
        m_gz_qa=m_gz_qa,
        m_gz_delta=m_gz_delta,
        cv_range=_range_dict({"tau_a": fold_tau_a, "tau_qa": fold_tau_qa, "delta": fold_delta}),
        n_gold=len(relevant),
        unreliable=len(relevant) < 20,
    )


def fit_temperature(
    unit_scores: np.ndarray,
    unit_labels: np.ndarray,
    grid: np.ndarray = TEMPERATURE_GRID,
) -> tuple[float, float]:
    """Fit a single temperature T by minimizing multi-label cross-entropy.

    Args:
        unit_scores: (n_units, n_codes) cosine scores.
        unit_labels: (n_units, n_codes) binary labels.
        grid: Candidate temperatures.

    Returns:
        (best_T, best_negative_log_likelihood).
    """
    best_t = 1.0
    best_loss = float("inf")
    for t in grid:
        logits = unit_scores / t
        # Add a dummy "none" logit at 0 for units with no label.
        none_logit = np.zeros((logits.shape[0], 1))
        all_logits = np.concatenate([logits, none_logit], axis=1)
        probs = np.exp(all_logits - np.max(all_logits, axis=1, keepdims=True))
        probs /= np.sum(probs, axis=1, keepdims=True)
        # Probability mass assigned to each true label.
        label_probs = probs[:, :-1] * unit_labels
        label_probs = np.clip(label_probs[unit_labels == 1], 1e-9, 1.0)
        loss = -np.mean(np.log(label_probs))
        if loss < best_loss:
            best_loss = float(loss)
            best_t = float(t)
    return best_t, best_loss


def expected_calibration_error(
    confidences: np.ndarray,
    accuracies: np.ndarray,
    bins: int = ECE_BINS,
) -> float:
    """ECE with ``bins`` equal-mass bins."""
    if len(confidences) == 0:
        return 0.0
    order = np.argsort(confidences)
    conf = confidences[order]
    acc = accuracies[order]
    bin_edges = np.linspace(0, len(conf), bins + 1, dtype=int)
    ece = 0.0
    total = len(conf)
    for b in range(bins):
        lo, hi = bin_edges[b], bin_edges[b + 1]
        if hi == lo:
            continue
        c_bin = conf[lo:hi]
        a_bin = acc[lo:hi]
        weight = len(c_bin) / total
        ece += weight * abs(np.mean(c_bin) - np.mean(a_bin))
    return float(ece)


def _raps_nonconformity(scores: np.ndarray, labels: np.ndarray) -> int:
    """Smallest top-k rank that covers all true labels for one unit."""
    true_codes = np.where(labels == 1)[0]
    if len(true_codes) == 0:
        return 0
    order = np.argsort(-scores)
    for k, _code in enumerate(order, start=1):
        if np.all(np.isin(true_codes, order[:k])):
            return k
    return scores.shape[0]


def conformal_sets(
    calibration_scores: np.ndarray,
    calibration_labels: np.ndarray,
    test_scores: np.ndarray,
    alpha: float = CONFORMAL_ALPHA,
) -> tuple[list[set[int]], float]:
    """Multi-label APS conformal prediction sets.

    Args:
        calibration_scores: (n_cal, n_codes) scores.
        calibration_labels: (n_cal, n_codes) binary labels.
        test_scores: (n_test, n_codes) scores.
        alpha: Miscoverage level (default 0.1 for 90% coverage).

    Returns:
        (list of predicted code-index sets, empirical coverage on calibration).
    """
    n_cal = len(calibration_scores)
    if n_cal == 0:
        return [set() for _ in range(len(test_scores))], 0.0
    nc = np.array(
        [_raps_nonconformity(calibration_scores[i], calibration_labels[i]) for i in range(n_cal)]
    )
    q = np.ceil((n_cal + 1) * (1 - alpha)) / n_cal
    # The rank quantile can exceed 1 for tiny calibration sets ((n+1)-th order
    # statistic is +infinity); clamping to 1.0 keeps the guarantee conservative.
    level = min(q, 1.0)
    k_hat = int(np.quantile(nc, level, method="higher"))
    k_hat = max(1, min(k_hat, int(calibration_scores.shape[1])))
    sets: list[set[int]] = []
    for scores in test_scores:
        top_k = np.argsort(-scores)[:k_hat]
        sets.append({int(i) for i in top_k})
    # Empirical coverage on calibration.
    covered = 0
    for i in range(n_cal):
        true_codes = {int(c) for c in np.where(calibration_labels[i] == 1)[0]}
        pred = {int(c) for c in np.argsort(-calibration_scores[i])[:k_hat]}
        if true_codes.issubset(pred):
            covered += 1
    coverage = covered / n_cal
    return sets, coverage
