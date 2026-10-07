"""Offline tests for keyword/exclusion synthesis (todo 17, v5 §13)."""

from __future__ import annotations

from collections import Counter

from corpus_kb.research.keyword_synthesis import (
    conflict_flags,
    ctfidf,
    hit_location,
    is_provisional,
    synthesize_exclusion_keywords,
    synthesize_keywords,
    tokenize,
)

THEME_A_TEXTS = [
    "billing workflow: the invoice queue stalls every monday and billing rechecks totals",
    "billing again: invoice totals mismatch after the billing export step",
    "billing staff recheck invoice totals before the billing close",
] * 3

THEME_B_TEXTS = [
    "scheduling conflict: the field visit calendar double-books technicians",
    "scheduling conflict again: technician calendars overlap on site visits",
    "scheduling desk resolves technician calendar conflicts weekly",
] * 3


def test_tokenize_drops_stopwords_and_short_terms():
    tokens = tokenize("The billing invoice is OK")
    assert "billing" in tokens
    assert "the" not in tokens
    assert "is" not in tokens


def test_synthesize_log_odds_distinctive_terms_top():
    result = synthesize_keywords({"code_a": THEME_A_TEXTS, "code_b": THEME_B_TEXTS})
    terms_a = [kw.term for kw in result["code_a"]]
    assert "billing" in terms_a[:3]
    terms_b = [kw.term for kw in result["code_b"]]
    assert "scheduling" in terms_b[:3]


def test_synthesize_ctfidf_method():
    result = synthesize_keywords(
        {"code_a": THEME_A_TEXTS, "code_b": THEME_B_TEXTS}, method="ctfidf"
    )
    assert all(kw.method == "ctfidf" for kw in result["code_a"])
    assert result["code_a"][0].score > 0


def test_synthesize_unknown_method_raises():
    try:
        synthesize_keywords({"a": ["x"]}, method="nope")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_provisional_floor_five_units():
    result = synthesize_keywords({"small": THEME_A_TEXTS[:5], "big": THEME_B_TEXTS}, min_support=8)
    assert result["small"][0].provisional is True
    assert result["big"][0].provisional is False
    assert is_provisional(5)
    assert not is_provisional(15)


def test_ctfidf_known_shape():
    scores = ctfidf(
        {
            "a": Counter({"billing": 9, "invoice": 6, "workflow": 3}),
            "b": Counter({"scheduling": 9, "calendar": 6, "workflow": 3}),
        }
    )
    assert scores["a"]["billing"] > scores["a"]["workflow"]
    assert scores["b"]["scheduling"] > 0


def test_hit_location_answer_question_both():
    assert hit_location("billing", "the billing queue", "why billing?") == "both"
    assert hit_location("billing", "billing queue", "nothing") == "answer"
    assert hit_location("billing", "nothing", "billing?") == "question"
    assert hit_location("billing", "nothing", "nothing") is None


def test_conflict_flags_shared_positive_and_exclusion():
    positives = synthesize_keywords(
        {
            "code_a": [*THEME_A_TEXTS, "shared billing term block"],
            "code_b": [*THEME_B_TEXTS[:2], "billing scheduling mixed unit here"],
        }
    )
    exclusions = synthesize_exclusion_keywords(
        ["billing rejected gray zone text about billing totals"],
        [*THEME_A_TEXTS, *THEME_B_TEXTS],
    )
    flags = conflict_flags(positives, exclusions)
    types = {f.conflict_type for f in flags}
    assert types  # at least one collision class fires on this corpus
    assert any(f.codes for f in flags)


def test_exclusion_contrast_widening_seeded():
    exclusions = synthesize_exclusion_keywords(
        ["deflected answer refusing the billing topic"],
        THEME_A_TEXTS,
        widen_pool_texts=["uncoded weather remark", "uncoded Smalltalk line"],
        widen_n=1,
        seed=42,
    )
    assert exclusions
    assert all(kw.kind == "exclusion" for kw in exclusions)
