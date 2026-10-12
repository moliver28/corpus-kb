"""Tests for the U16 top-term Jaccard content-stability metric (additive)."""

from __future__ import annotations

from corpus_kb.research.cluster_stability import ari, top_term_jaccard


def test_identical_runs_score_one() -> None:
    runs = [
        ["economy", "jobs", "tax", "debt"],
        ["economy", "jobs", "tax", "debt"],
    ]
    result = top_term_jaccard(runs, k=3)
    assert result.mean_jaccard == 1.0
    assert result.n_runs == 2
    assert result.k == 3


def test_disjoint_runs_score_zero() -> None:
    runs = [
        ["a", "b", "c", "d"],
        ["w", "x", "y", "z"],
    ]
    assert top_term_jaccard(runs, k=4).mean_jaccard == 0.0


def test_partial_overlap_scores_between() -> None:
    runs = [
        ["a", "b", "c", "d"],
        ["a", "b", "x", "y"],
    ]
    # Top-3 sets {a,b,c} vs {a,b,x}: Jaccard 2/4 = 0.5.
    assert top_term_jaccard(runs, k=3).mean_jaccard == 0.5


def test_k_truncation_uses_only_top_k() -> None:
    runs = [
        ["a", "b", "c", "zzz"],
        ["a", "b", "c", "www"],
    ]
    # With k=3 the tail terms are ignored -> perfect agreement.
    assert top_term_jaccard(runs, k=3).mean_jaccard == 1.0
    # With k=4 the differing tails drag it down to J({a,b,c,zzz},{a,b,c,www}) = 3/5.
    assert top_term_jaccard(runs, k=4).mean_jaccard == 3.0 / 5.0


def test_three_runs_pairwise_mean() -> None:
    runs = [
        ["a", "b"],
        ["a", "b"],
        ["c", "d"],
    ]
    result = top_term_jaccard(runs, k=2)
    # Pairs: (0,1)=1.0, (0,2)=0.0, (1,2)=0.0 -> mean 1/3.
    assert result.mean_jaccard is not None
    assert abs(result.mean_jaccard - 1.0 / 3.0) < 1e-12


def test_single_run_is_none_not_one() -> None:
    result = top_term_jaccard([["a", "b"]], k=2)
    assert result.mean_jaccard is None  # honest not-evaluable
    assert result.n_runs == 1


def test_membership_stability_untouched() -> None:
    # The pre-existing ARI path is unchanged by the additive U16 work.
    assert ari([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0
