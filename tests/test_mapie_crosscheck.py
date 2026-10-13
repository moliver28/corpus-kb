"""MAPIE cross-check of the repository's conformal prediction (issue #78, U28).

Role of MAPIE
-------------
MAPIE (BSD-3-Clause) is an **offline, tests-only** independent re-implementation
used to cross-check ``corpus_kb.research.calibration.conformal_sets`` — the
gray-zone / conformal-posture layer. It is never a runtime dependency: it lives
in the ``dev`` extras only, and this module is skipped (never failing) when it
is absent.

Experimental design
-------------------
Both implementations consume the *identical* fixed-seed synthetic data:

1. A seeded ``default_rng`` draws latent scores for ``N_CAL + N_TEST`` units
   over ``N_CODES`` codes. A per-unit "true" code receives a signal boost
   drawn from ``Gamma(4.0, 0.8)`` (5% of units get no boost — the gray zone),
   on top of ``N(0, 1)`` noise; softmax turns latent scores into a
   probability matrix. Softmax is strictly rank-preserving per row, so the
   repository's rank-based method is invariant to it.
2. The first ``N_CAL`` units calibrate, the remaining ``N_TEST`` units test.
   MAPIE's ``SplitConformalClassifier(prefit=True)`` reads the very same
   probability matrix through an index-keyed ``predict_proba`` table, so any
   coverage difference is attributable to the conformal layer alone — no
   estimator-training noise.

Scope restriction: the repository's method is multi-label (a prediction set
must contain *all* true codes); MAPIE's classification API is single-label.
The cross-check therefore instantiates the single-label case (exactly one
true code per unit), where the repository's rank nonconformity reduces to the
rank of the true code — directly comparable to MAPIE.

Divergence findings (measured with mapie 1.5.0 / numpy 2.4.6, seeds 78/178/
278/378/478, n_codes=8, n_cal=400, n_test=600, alpha=0.1, random_state pinned)
------------------------------------------------------------------------------
* **vs MAPIE ``"top_k"``** (the exact mathematical equivalent of the repo's
  rank nonconformity): empirical test coverage agrees to 4 decimals on all
  five seeds (repo 0.9200/0.9250/0.9183/0.9267/0.9350 vs MAPIE identical),
  and per-unit prediction sets are identical whenever k̂ matches. The
  quantile levels differ by construction — the repo uses
  ``ceil((n+1)(1-alpha))/n`` (deliberately conservative, see its clamp
  comment) while MAPIE uses the unceiled ``(n+1)(1-alpha)/n`` — so with
  ``method="higher"`` the repo's k̂ can land *one rank higher*. An extreme
  weak-signal probe sweep (near-flat probabilities) observed exactly this
  once (repo k̂=8 vs MAPIE k̂=7, |Δcoverage| = 0.105 — the repo saturates to
  the full code set, coverage 1.0, while MAPIE stays at 0.895). The test
  asserts k̂_repo ∈ {k̂_mapie, k̂_mapie + 1} and uses tolerance 0.02, which
  covers the regime the test instantiates.
* **vs MAPIE ``"aps"``** (the method the repo's docstring names): structural
  divergence. The repo emits one *fixed* rank-k set size for every unit
  (k̂ = 3 or 4 on these seeds); APS thresholds the *cumulative* probability
  per unit, so sets adapt — modal size 1 on confident units, large on
  gray-zone units, which is exactly where deep-ranked true codes live.
  Measured test coverage, repo vs MAPIE APS:
  0.9200/0.9250/0.9183/0.9267/0.9350 vs 0.9500/0.9800/0.9500/0.9600/0.9467.
  MAPIE APS ran +0.0117 to +0.0550 *above* the repo on every seed in this
  regime (directional consistency is asserted here); tolerance 0.06 covers
  the observed maximum gap of 0.055. The direction is regime-dependent and
  intentionally **not** asserted as a universal claim: in a weak-signal
  probe sweep the repo was the more conservative (e.g. 0.9267 vs 0.8950 on
  one probe seed), because its ceil-biased quantile level then buys coverage
  the adaptive threshold does not need.
* **MAPIE APS is stochastic unless pinned.** Its cumulative-rank thresholds
  are randomized (uniform tie-breaking); with ``random_state=None`` the same
  inputs wobbled by about ±0.01 across runs here (0.9600 vs 0.9533 on seed
  278). Both MAPIE runs below therefore pass ``random_state=seed`` — see
  ``_mapie_test_coverage``.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("mapie")

ALPHA = 0.1
N_CODES = 8
N_CAL = 400
N_TEST = 600
SEEDS = (78, 178, 278, 378, 478)
# Medium-signal regime: most units easy, ~5% gray-zone units with no boost.
SIGNAL_SHAPE = 4.0
SIGNAL_SCALE = 0.8
NO_BOOST_FRAC = 0.05
# Tolerances documented in the module docstring; measured, not aspirational.
TOL_TOPK_COVERAGE = 0.02
TOL_APS_COVERAGE = 0.06
# A single fixed split may undershoot the 1-alpha nominal level; the
# distribution-free guarantee is marginal (in expectation over splits).
COVERAGE_FLOOR = 0.85


def _softmax_rows(raw: np.ndarray) -> np.ndarray:
    shifted = raw - raw.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def _synthetic_probabilities(
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Probability matrix and single true-code label per unit, fixed seed."""
    rng = np.random.default_rng(seed)
    labels = rng.integers(0, N_CODES, N_CAL + N_TEST)
    raw = rng.normal(0.0, 1.0, (len(labels), N_CODES))
    boost = np.where(
        rng.random(len(labels)) < NO_BOOST_FRAC,
        0.0,
        rng.gamma(SIGNAL_SHAPE, SIGNAL_SCALE, len(labels)),
    )
    raw[np.arange(len(labels)), labels] += boost
    return _softmax_rows(raw), labels


def _repo_conformal_test_coverage(
    cal_probs: np.ndarray, cal_labels: np.ndarray, test_probs: np.ndarray, test_labels: np.ndarray
) -> tuple[float, int, list[set[int]]]:
    """Run the repository's conformal_sets; return (coverage, k-hat, sets)."""
    from corpus_kb.research.calibration import conformal_sets

    onehot = np.zeros((len(cal_labels), N_CODES), dtype=int)
    onehot[np.arange(len(cal_labels)), cal_labels] = 1
    sets, _cal_coverage = conformal_sets(cal_probs, onehot, test_probs, alpha=ALPHA)
    covered = sum(1 for i, s in enumerate(sets) if int(test_labels[i]) in s)
    # All repo sets have exactly k-hat members (top-k of distinct scores).
    k_hat = len(sets[0])
    return covered / len(sets), k_hat, sets


def _mapie_test_coverage(
    probs: np.ndarray, labels: np.ndarray, method: str, random_state: int
) -> tuple[float, int, np.ndarray]:
    """Run MAPIE on the same matrix; return (coverage, k-hat, membership).

    MAPIE never scores the calibration rows it calibrates on, so calibration
    uses rows ``[:N_CAL]`` and prediction rows ``[N_CAL:]`` — the exact split
    the repository implementation sees.

    ``random_state`` must be pinned: MAPIE's APS score randomizes the
    cumulative-rank thresholds (uniform tie-breaking), so with the default
    ``random_state=None`` the same inputs yield run-to-run coverage wobble of
    about ±0.01 here (measured: 0.9600 vs 0.9533 on seed 278). Pinning makes
    both the assertions and the docstring numbers reproducible.
    """
    from sklearn.base import BaseEstimator, ClassifierMixin

    class _ProbaTable(BaseEstimator, ClassifierMixin):
        """Index-keyed table: predict_proba(i) returns probs[i], verbatim."""

        def __init__(self, table: np.ndarray) -> None:
            self.table = table

        def fit(self, x: np.ndarray, y: object = None) -> _ProbaTable:
            self.classes_ = np.arange(self.table.shape[1])
            return self

        def predict_proba(self, x: np.ndarray) -> np.ndarray:
            return self.table[np.asarray(x, dtype=int)]

    from mapie.classification import SplitConformalClassifier

    estimator = _ProbaTable(probs).fit(np.arange(len(labels)))
    classifier = SplitConformalClassifier(
        estimator=estimator,
        confidence_level=1 - ALPHA,
        conformity_score=method,
        prefit=True,
        random_state=random_state,
    )
    classifier.conformalize(np.arange(N_CAL), labels[:N_CAL])
    _y_pred, prediction_sets = classifier.predict_set(np.arange(N_CAL, N_CAL + N_TEST))
    membership = np.asarray(prediction_sets)[:, :, 0].astype(bool)
    covered = int(np.sum(membership[np.arange(N_TEST), labels[N_CAL:]]))
    # k-hat is the (fixed, for top_k) modal set size; aps sets vary per unit.
    k_hat = int(np.bincount(membership.sum(axis=1)).argmax())
    return covered / N_TEST, k_hat, membership


@pytest.mark.parametrize("seed", SEEDS)
def test_mapie_top_k_matches_repo_rank_method(seed: int) -> None:
    """Repo rank method vs MAPIE top_k: same math, must agree tightly."""
    probs, labels = _synthetic_probabilities(seed)
    cal_probs, cal_labels = probs[:N_CAL], labels[:N_CAL]
    test_probs, test_labels = probs[N_CAL:], labels[N_CAL:]

    repo_cov, repo_k, repo_sets = _repo_conformal_test_coverage(
        cal_probs, cal_labels, test_probs, test_labels
    )
    mapie_cov, mapie_k, mapie_sets = _mapie_test_coverage(
        probs, labels, method="top_k", random_state=seed
    )

    # Quantile-level conservatism direction (docstring: ceil vs unceiled).
    assert repo_k >= mapie_k, f"repo k-hat {repo_k} unexpectedly below MAPIE {mapie_k}"
    assert repo_k - mapie_k <= 1, f"quantile off-by-one blew up: {repo_k} vs {mapie_k}"
    if repo_k == mapie_k:
        for i in range(N_TEST):
            assert repo_sets[i] == set(np.flatnonzero(mapie_sets[i])), (
                f"seed {seed} unit {i}: sets differ despite identical k-hat"
            )
    assert abs(repo_cov - mapie_cov) <= TOL_TOPK_COVERAGE, (
        f"seed {seed}: repo coverage {repo_cov:.4f} vs MAPIE top_k {mapie_cov:.4f}"
    )
    assert repo_cov >= COVERAGE_FLOOR and mapie_cov >= COVERAGE_FLOOR


@pytest.mark.parametrize("seed", SEEDS)
def test_mapie_aps_within_documented_tolerance(seed: int) -> None:
    """Repo vs MAPIE aps: structural divergence, bounded by the 0.06 tolerance.

    The repo's fixed rank-k sets and MAPIE's adaptive cumulative-probability
    sets are different procedures targeting the same 1-alpha level; see the
    module docstring for the measured systematic gap (+0.012..+0.055 for
    MAPIE in this regime). Both must stay marginally valid and close.
    """
    probs, labels = _synthetic_probabilities(seed)
    cal_probs, cal_labels = probs[:N_CAL], labels[:N_CAL]
    test_probs, test_labels = probs[N_CAL:], labels[N_CAL:]

    repo_cov, _repo_k, repo_sets = _repo_conformal_test_coverage(
        cal_probs, cal_labels, test_probs, test_labels
    )
    mapie_cov, _mapie_k, mapie_sets = _mapie_test_coverage(
        probs, labels, method="aps", random_state=seed
    )

    assert all(len(s) > 0 for s in repo_sets), "repo produced an empty set"
    assert bool(mapie_sets.any(axis=1).all()), "MAPIE produced an empty set"
    assert repo_cov >= COVERAGE_FLOOR, f"seed {seed}: repo coverage {repo_cov:.4f} below floor"
    assert mapie_cov >= COVERAGE_FLOOR, f"seed {seed}: MAPIE coverage {mapie_cov:.4f} below floor"
    # Directional consistency in this regime (docstring): the adaptive APS
    # sets are never less conservative than the fixed rank-k sets here. This
    # is a regime-scoped observation, not a universal conformal claim.
    assert mapie_cov >= repo_cov, (
        f"seed {seed}: adaptive APS coverage {mapie_cov:.4f} fell below fixed-k repo {repo_cov:.4f}"
    )
    assert abs(repo_cov - mapie_cov) <= TOL_APS_COVERAGE, (
        f"seed {seed}: repo {repo_cov:.4f} vs MAPIE aps {mapie_cov:.4f} "
        f"exceeds documented tolerance {TOL_APS_COVERAGE}"
    )
