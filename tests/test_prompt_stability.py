"""Tests for the U12 prompt-stability computation (krippendorff alpha gate math)."""

from __future__ import annotations

from corpus_kb.coding.uncertainty.prompt_stability import prompt_stability


def test_identical_variants_pass_with_alpha_one() -> None:
    v1 = {"u1": "theme_a", "u2": "theme_b", "u3": "theme_c"}
    v2 = {"u1": "theme_a", "u2": "theme_b", "u3": "theme_c"}
    result = prompt_stability([v1, v2], alpha_threshold=0.667)
    assert result.status == "pass"
    assert result.alpha == 1.0
    assert result.n_variants == 2
    assert result.n_units == 3
    assert "identical" in result.reason


def test_divergent_variants_fail() -> None:
    v1 = {"u1": "theme_a", "u2": "theme_b", "u3": "theme_a", "u4": "theme_b"}
    v2 = {"u1": "theme_b", "u2": "theme_a", "u3": "theme_b", "u4": "theme_a"}
    v3 = {"u1": "theme_a", "u2": "theme_a", "u3": "theme_b", "u4": "theme_a"}
    result = prompt_stability([v1, v2, v3], alpha_threshold=0.9)
    assert result.status == "fail"
    assert result.alpha is not None
    assert result.alpha < 0.9


def test_single_variant_not_evaluable() -> None:
    result = prompt_stability([{"u1": "a"}], alpha_threshold=0.667)
    assert result.status == "not_evaluable"
    assert result.alpha is None


def test_no_units_not_evaluable() -> None:
    result = prompt_stability([{}, {}], alpha_threshold=0.667)
    assert result.status == "not_evaluable"


def test_agreeing_variants_pass_threshold() -> None:
    # Mostly-agreeing variants with a little noise land at a middling alpha:
    # pass at the tentative 0.5 threshold, fail at a demanding 0.99 one.
    v1 = {"u1": "a", "u2": "b", "u3": "c", "u4": "d", "u5": "e", "u6": "f"}
    v2 = {"u1": "a", "u2": "b", "u3": "c", "u4": "d", "u5": "e", "u6": "a"}
    pass_result = prompt_stability([v1, v2], alpha_threshold=0.5)
    fail_result = prompt_stability([v1, v2], alpha_threshold=0.99)
    assert pass_result.status == "pass"
    assert pass_result.alpha is not None
    assert pass_result.alpha >= 0.5
    assert fail_result.status == "fail"


def test_missing_unit_in_one_variant_is_missing_data() -> None:
    # v2 never labeled u6: that unit carries one rating only and drops out
    # of the alpha instead of counting as a fake disagreement.
    v1 = {"u1": "a", "u2": "b", "u3": "c", "u6": "f"}
    v2 = {"u1": "a", "u2": "b", "u3": "c"}
    result = prompt_stability([v1, v2], alpha_threshold=0.667)
    assert result.status == "pass"
    assert result.n_units == 4  # unit count reflects the union seen
