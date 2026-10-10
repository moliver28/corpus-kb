"""U48 multi-run consensus — quote gating, theme clustering, run agreement."""

from __future__ import annotations

import pytest

from corpus_kb.research.release.consensus import (
    QUOTE_NOT_FOUND,
    Candidate,
    aggregate_runs,
    build_themes,
    verify_quotes,
)


def _candidate(
    run_id: str,
    label: str,
    embedding: list[float],
    *,
    quote: str = "prices went up",
    unit_text: str = "and then prices went up last quarter",
) -> Candidate:
    return Candidate(
        run_id=run_id,
        label=label,
        definition=f"{label}: concern about cost",
        embedding=embedding,
        quote=quote,
        unit_id=1,
        unit_text=unit_text,
    )


SAME_THEME_A = [1.0, 0.0, 0.0]
SAME_THEME_B = [0.95, 0.05, 0.0]
OTHER_THEME = [0.0, 1.0, 0.0]


def test_verify_quotes_rejects_not_found_and_keeps_matches():
    good = _candidate("r0", "cost", SAME_THEME_A)
    normalized = _candidate(
        "r0",
        "cost2",
        SAME_THEME_B,
        quote="prices  went up",  # whitespace-collapse repair path
    )
    fabricated = _candidate(
        "r0", "fake", OTHER_THEME, quote="we love price hikes", unit_text="no such claim here"
    )
    ok, rejected = verify_quotes([good, normalized, fabricated])
    assert [c.label for c in ok] == ["cost", "cost2"]
    assert len(rejected) == 1
    assert rejected[0].reason == QUOTE_NOT_FOUND
    assert rejected[0].candidate.label == "fake"


def test_build_themes_clusters_by_similarity_and_reports_stats():
    candidates = [
        _candidate("r0", "cost", SAME_THEME_A),
        _candidate("r0", "cost-echo", SAME_THEME_B),
        _candidate("r0", "delivery", OTHER_THEME),
    ]
    themes = build_themes(candidates, similarity_threshold=0.9)
    assert len(themes) == 2
    cost_theme = next(t for t in themes if t.representative().label in {"cost", "cost-echo"})
    assert cost_theme.mean_similarity >= 0.9
    assert cost_theme.min_similarity >= 0.9
    assert cost_theme.supporting_runs == {"r0"}


def test_aggregate_runs_keeps_themes_meeting_the_run_fraction():
    runs = [
        [_candidate("r0", "cost", SAME_THEME_A), _candidate("r0", "delivery", OTHER_THEME)],
        [_candidate("r1", "cost", SAME_THEME_B), _candidate("r1", "delivery", OTHER_THEME)],
        [_candidate("r2", "cost", SAME_THEME_A), _candidate("r2", "delivery", OTHER_THEME)],
    ]
    result = aggregate_runs(runs, min_run_fraction=2 / 3, similarity_threshold=0.9)
    assert result.n_runs == 3
    assert result.themes_kept == 2
    assert result.quote_not_found_rejected == 0
    labels = {t.representative().label for t in result.kept}
    assert labels == {"cost", "delivery"}
    for theme in result.kept:
        assert len(theme.supporting_runs) == 3
    assert result.run_to_run_agreement
    assert all(score == 1.0 for score in result.run_to_run_agreement.values())


def test_aggregate_runs_drops_themes_below_the_fraction():
    runs = [
        [_candidate("r0", "cost", SAME_THEME_A)],
        [_candidate("r1", "delivery", OTHER_THEME)],
        [_candidate("r2", "delivery", OTHER_THEME)],
    ]
    result = aggregate_runs(runs, min_run_fraction=2 / 3, similarity_threshold=0.9)
    assert result.themes_kept == 1
    assert result.kept[0].representative().label == "delivery"


def test_aggregate_runs_rejects_invalid_thresholds():
    with pytest.raises(ValueError, match="min_run_fraction"):
        aggregate_runs([[]], min_run_fraction=1.5, similarity_threshold=0.9)
    with pytest.raises(ValueError, match="similarity_threshold"):
        aggregate_runs([[]], min_run_fraction=0.5, similarity_threshold=2.0)


def test_quote_not_found_candidates_never_vote():
    fabricated = _candidate("r0", "phantom", SAME_THEME_A, quote="never said this")
    legit_r0 = _candidate("r0", "cost", SAME_THEME_B)
    runs = [
        [fabricated, legit_r0],
        [_candidate("r1", "cost", SAME_THEME_A)],
        [_candidate("r2", "cost", SAME_THEME_A)],
    ]
    result = aggregate_runs(runs, min_run_fraction=0.99, similarity_threshold=0.9)
    assert result.quote_not_found_rejected == 1
    kept_labels = [t.representative().label for t in result.kept]
    assert "phantom" not in kept_labels
    assert "cost" in kept_labels
    rejected_keys = [r.candidate.key() for r in result.rejected]
    assert rejected_keys == ["r0:0"]
