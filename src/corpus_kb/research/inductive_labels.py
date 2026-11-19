"""Deterministic cluster labeling for the inductive engine.

Extracted from ``inductive_run`` (zero-drift rule 7: that file sat at its
ratchet ceiling, and ceilings never rise). ``suggested_label`` stays
importable from ``inductive_run`` through a re-export.
"""

from __future__ import annotations

from collections import Counter

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "with",
        "you",
        "your",
    ]
)


def suggested_label(summaries: list[str]) -> str:
    """Deterministic top-content-terms label (ties break alphabetically)."""
    counts: Counter[str] = Counter()
    for text in summaries:
        for token in text.lower().split():
            word = "".join(ch for ch in token if ch.isalnum())
            if len(word) > 2 and word not in _STOPWORDS:
                counts[word] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top = [w for w, _ in ranked[:3]]
    return "-".join(top) if top else "cluster"
