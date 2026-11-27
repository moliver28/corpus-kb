"""Keyword block rule (TD-10): sentence-level exclusion over include hits."""

from __future__ import annotations

import pytest

from corpus_kb.coding.codebook import CodebookError, validate_codebook
from corpus_kb.coding.keyword_authoring import (
    exclusion_blocked,
    keyword_tag_decision,
    split_sentences,
)


def test_split_sentences_is_deterministic() -> None:
    text = "One fact. Two facts! Three? trailing clause"
    assert split_sentences(text) == ["One fact.", "Two facts!", "Three?", "trailing clause"]
    assert split_sentences("") == []


def test_include_hit_without_exclusion_tags() -> None:
    outcome = exclusion_blocked("The safety valve held.", ["safety"], ["risk"])
    assert outcome == {
        "tag": True,
        "blocked": False,
        "include_hits": ["safety"],
        "block_terms": [],
    }


def test_exclude_in_same_sentence_blocks_the_tag() -> None:
    text = "The safety valve held. Risk was low but safety worked."
    outcome = exclusion_blocked(text, ["safety"], ["risk"])
    assert outcome["blocked"] is True
    assert outcome["tag"] is False
    assert outcome["block_terms"] == ["risk"]


def test_exclude_in_other_sentence_does_not_block() -> None:
    text = "Safety held. The risk passed later."
    assert exclusion_blocked(text, ["safety"], ["risk"])["tag"] is True


def test_word_boundary_matching_ignores_substrings() -> None:
    outcome = exclusion_blocked("The bipartisan bill passed.", ["part"], [])
    assert outcome["include_hits"] == []
    assert outcome["tag"] is False


def test_no_include_hit_is_no_tag_and_no_block() -> None:
    outcome = exclusion_blocked("Nothing applies here.", ["safety"], ["risk"])
    assert outcome == {"tag": False, "blocked": False, "include_hits": [], "block_terms": []}


@pytest.mark.parametrize(
    ("keywords", "expect_tag"),
    [
        ({"include": ["safety"]}, True),
        ({"include": ["safety"], "exclude": ["risk"]}, True),
        (None, False),
        ({"exclude": ["risk"]}, False),
        ({}, False),
    ],
)
def test_keyword_tag_decision_reasons(
    keywords: dict[str, list[str]] | None, expect_tag: bool
) -> None:
    outcome = keyword_tag_decision("Safety worked as planned.", keywords)
    assert outcome["tag"] is expect_tag
    assert isinstance(outcome["reason"], str)


def _code(code_id: str, keywords: dict[str, list[str]] | None) -> dict[str, object]:
    return {
        "id": code_id,
        "name": "n",
        "brief_definition": "b",
        "inclusion_criteria": "i",
        "exclusion_criteria": "e",
        "examples": [],
        "theory": {},
        "keywords": keywords,
    }


def test_validate_codebook_accepts_wellformed_keywords() -> None:
    good = _code("C1", {"include": ["a"], "exclude": ["b"]})
    validate_codebook({"paradigm": "codebook", "categories": [good]})


def test_validate_codebook_rejects_unknown_keyword_keys() -> None:
    with pytest.raises(CodebookError, match="unknown keys"):
        validate_codebook({"paradigm": "codebook", "categories": [_code("C1", {"block": ["a"]})]})


def test_validate_codebook_rejects_non_string_terms() -> None:
    with pytest.raises(CodebookError, match="list of strings"):
        validate_codebook({"paradigm": "codebook", "categories": [_code("C1", {"include": [1]})]})


def test_exclusion_list_needs_inclusion_or_criteria_text() -> None:
    bare = {
        "id": "C2",
        "name": "n",
        "brief_definition": "b",
        "inclusion_criteria": "",
        "exclusion_criteria": "",
        "examples": [],
        "theory": {},
        "keywords": {"exclude": ["x"]},
    }
    with pytest.raises(CodebookError, match="needs something to block"):
        validate_codebook({"paradigm": "codebook", "categories": [bare]})
    bare["exclusion_criteria"] = "no prior incidents"
    validate_codebook({"paradigm": "codebook", "categories": [bare]})  # criteria text unblocks
