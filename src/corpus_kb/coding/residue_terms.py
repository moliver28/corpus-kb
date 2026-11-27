"""Residue-cluster top terms (TD-11): deterministic word counts, no model.

Split out of mece_report.py so that file stays inside the 250-line gate.
The stopword set is an owned convention (C12); order is count desc, then
word, so the terms are stable for snapshots.
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
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "not",
        "of",
        "on",
        "or",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
    ]
)


def residue_top_terms(texts_by_label: dict[int, list[str]]) -> list[dict[str, object]]:
    """Top five words per residue cluster, deterministic order (count, then word)."""
    clusters = []
    for label in sorted(texts_by_label):
        words: Counter[str] = Counter()
        for text in texts_by_label[label]:
            words.update(w for w in text.lower().split() if w.isalpha() and w not in _STOPWORDS)
        top = sorted(words.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        clusters.append(
            {"cluster": label, "n_chunks": len(texts_by_label[label]), "top_terms": top}
        )
    return clusters
