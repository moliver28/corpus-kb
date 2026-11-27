"""Keyword tagging rules: sentence-level exclusion blocks (TD-10).

An include hit is evidence for a tag; an exclude keyword in the SAME
sentence as an include hit blocks the automatic tag. This never deletes a
human code (owned convention, S9; Carter 2018; Harduar Morano and Waller
2017). The sentence regex follows ``research/notebook.py``; this module
owns its copy so coding does not import the ask domain. Matching is
word-boundary, case-insensitive, like ``pooling.py``.
"""

from __future__ import annotations

import re

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

__all__ = ["exclusion_blocked", "keyword_tag_decision", "split_sentences"]


def split_sentences(text: str) -> list[str]:
    """Split one chunk text into sentences (deterministic, stdlib regex)."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]


def _term_pattern(term: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive pattern for one keyword term."""
    return re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)


def _sentences_with_hits(sentences: list[str], terms: list[str]) -> dict[int, set[str]]:
    """Sentence index -> the terms of `terms` that match inside it."""
    if not terms:
        return {}
    patterns = [(term, _term_pattern(term)) for term in terms]
    hits: dict[int, set[str]] = {}
    for index, sentence in enumerate(sentences):
        matched = {term for term, pattern in patterns if pattern.search(sentence)}
        if matched:
            hits[index] = matched
    return hits


def exclusion_blocked(
    text: str,
    include_terms: list[str],
    exclude_terms: list[str],
) -> dict[str, object]:
    """The block rule: ``tag`` only when no exclude term shares a hit sentence.

    Returns ``{"tag": bool, "blocked": bool, "include_hits": [str],
    "block_terms": [str]}``; sorted term lists keep it deterministic.
    """
    sentences = split_sentences(text)
    include_hits = _sentences_with_hits(sentences, include_terms)
    if not include_hits:
        return {"tag": False, "blocked": False, "include_hits": [], "block_terms": []}
    exclude_hits = _sentences_with_hits(sentences, exclude_terms)
    block_terms: set[str] = set()
    for index in include_hits:
        block_terms |= exclude_hits.get(index, set())
    blocked = bool(block_terms)
    return {
        "tag": not blocked,
        "blocked": blocked,
        "include_hits": sorted({t for terms in include_hits.values() for t in terms}),
        "block_terms": sorted(block_terms),
    }


def keyword_tag_decision(
    text: str,
    keywords: dict[str, list[str]] | None,
) -> dict[str, object]:
    """One chunk x code decision from an authored ``keywords`` block (may be None).

    With include terms alone, any hit tags. The answer names the evidence
    so a report can quote it; it never touches stored human codes.
    """
    include = list((keywords or {}).get("include") or [])
    exclude = list((keywords or {}).get("exclude") or [])
    if not include:
        return {
            "tag": False,
            "blocked": False,
            "include_hits": [],
            "block_terms": [],
            "reason": "no include keywords",
        }
    outcome = exclusion_blocked(text, include, exclude)
    if not outcome["include_hits"]:
        outcome["reason"] = "no include keyword hit"
    elif outcome["blocked"]:
        outcome["reason"] = "exclude keyword shares the sentence with an include hit"
    else:
        outcome["reason"] = "include keyword hit, no exclusion"
    return outcome
