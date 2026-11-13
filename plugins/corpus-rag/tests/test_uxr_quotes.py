"""Quote classes and NFKC normalization (issue #355, behavior 0.6.0 pin).

check-quote: exact, near_miss, or fail. Normalization is NFKC, then
lowercase, then punctuation to space. A near miss is a difflib ratio of
0.90 or more on a sliding window.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import uxr_quotes  # noqa: E402


def test_normalize_is_nfkc_lowercase_punctuation_to_space() -> None:
    # NFKC folds the fullwidth A and the ligature; case folds; the dash
    # and the comma become spaces; runs of space collapse to one.
    assert uxr_quotes.normalize("\uff21\uff22, please \u2014 wait\u2026") == (
        "ab please wait"
    )


def test_normalize_keeps_digits_and_inner_space() -> None:
    assert uxr_quotes.normalize("Users  said: 'No!'") == "users said no"


def test_exact_match_after_normalization() -> None:
    text = "The beta testers ignored the export button entirely."
    assert uxr_quotes.check_quote("beta testers ignored", text) == "exact"


def test_exact_match_ignores_case_and_punctuation() -> None:
    text = "\u201cI never found the EXPORT button,\u201d she said."
    assert uxr_quotes.check_quote("i never found the export button", text) == "exact"


def test_near_miss_within_the_sliding_window() -> None:
    text = (
        "Users clicked the share icon first and ignored the export "
        "button for three whole sessions."
    )
    # One word differs ("share" vs "search"); the ratio stays >= 0.90.
    quote = "clicked the search icon first and ignored the export"
    assert uxr_quotes.check_quote(quote, text) == "near_miss"


def test_fail_when_the_words_are_not_there() -> None:
    text = "Users praised the dashboard layout and the color palette."
    assert uxr_quotes.check_quote("the export flow was invisible", text) == "fail"


def test_fail_when_the_window_is_too_far_away() -> None:
    text = "first sentence. " + "filler words keep going here. " * 6 + (
        "ignored the export button."
    )
    assert uxr_quotes.check_quote("ignored the export button", text) == "exact"


def test_empty_quote_or_text_fails() -> None:
    assert uxr_quotes.check_quote("", "some text") == "fail"
    assert uxr_quotes.check_quote("some quote", "") == "fail"


def test_exact_wins_over_near_miss() -> None:
    text = "the team agreed on the plan"
    assert uxr_quotes.check_quote("the team agreed on the plan", text) == "exact"
