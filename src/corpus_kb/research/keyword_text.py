"""Text/tokenization + class-based TF-IDF for keyword synthesis (todo 17).

Split from keyword_synthesis.py to honor the 250-line soft limit: THIS
module owns the corpus-text mechanics (tokenization, stopwords, c-TF-IDF);
keyword_synthesis.py owns the synthesis policy (positive/exclusion lists,
support floors, conflict flags).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

_TOKEN_RE = re.compile(r"[a-z][a-z'-]{2,}")

STOPWORDS = frozenset(
    [
        "a",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "being",
        "both",
        "but",
        "by",
        "came",
        "can",
        "did",
        "does",
        "doing",
        "each",
        "even",
        "every",
        "few",
        "for",
        "from",
        "get",
        "got",
        "had",
        "has",
        "have",
        "her",
        "here",
        "him",
        "his",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "like",
        "lot",
        "many",
        "may",
        "more",
        "most",
        "much",
        "not",
        "now",
        "of",
        "on",
        "only",
        "or",
        "other",
        "our",
        "out",
        "per",
        "really",
        "said",
        "same",
        "she",
        "should",
        "some",
        "such",
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
        "those",
        "through",
        "to",
        "too",
        "very",
        "want",
        "was",
        "way",
        "we",
        "well",
        "went",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "will",
        "with",
        "would",
        "yeah",
        "you",
        "your",
    ]
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens (>=3 chars) minus stopwords."""
    return [w for w in (m.group(0) for m in _TOKEN_RE.finditer(text.lower())) if w not in STOPWORDS]


def term_counts(texts: Sequence[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(tokenize(text))
    return counts


def ctfidf(counts_by_class: Mapping[str, Counter[str]]) -> dict[str, dict[str, float]]:
    """Class-based TF-IDF (BERTopic form) over per-class term counters.

    tf(c,t) = count(c,t) / total(c); idf(t) = log(1 + A / freq_avg(t)) with
    A the number of classes and freq_avg the term's average count per class.
    """
    n_classes = max(1, len(counts_by_class))
    totals = {c: sum(counts.values()) or 1 for c, counts in counts_by_class.items()}
    all_terms: set[str] = set()
    for counts in counts_by_class.values():
        all_terms.update(counts)
    scores: dict[str, dict[str, float]] = {}
    for code_id, counts in counts_by_class.items():
        code_scores: dict[str, float] = {}
        for term in all_terms:
            count = counts.get(term, 0)
            if count == 0:
                continue
            freq_avg = sum(c.get(term, 0) for c in counts_by_class.values()) / n_classes
            idf = math.log1p(n_classes / freq_avg) if freq_avg > 0 else 0.0
            code_scores[term] = (count / totals[code_id]) * idf
        scores[code_id] = code_scores
    return scores
