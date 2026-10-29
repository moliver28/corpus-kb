"""The textbook scan signals (textbook-ingestion program, Wave 2C).

The eight deterministic signals and their measurement, split from
``textbook_scan`` to keep both files under the size limit. Every
per-page signal divides by a documented cap, so each lands in [0, 1];
the weights live in ``SCAN_WEIGHTS`` and sum to 1.0. Same pages in, same
signals out — no locale, no randomness, no network.
"""

from __future__ import annotations

import re
from itertools import pairwise

SCAN_WEIGHTS: dict[str, float] = {
    "text_layer": 0.20,
    "outline": 0.20,
    "definitions": 0.15,
    "xrefs": 0.10,
    "hedging": 0.10,
    "tables": 0.05,
    "index_glossary": 0.05,
    "overlap": 0.15,
}
#: Mean matches per sampled page that pin each regex signal at 1.0.
SIGNAL_CAPS: dict[str, float] = {
    "definitions": 3.0,
    "xrefs": 5.0,
    "hedging": 4.0,
    "tables": 10.0,
    "index_glossary": 5.0,
}
#: A sampled page counts toward text_layer at this many extractable chars.
MIN_TEXT_CHARS = 200
#: The token-set size behind the overlap signal.
TOP_TOKENS = 25

_DEFINITIONS_RX = re.compile(
    r"\b(?:is defined as|is called|known as|refers to|denotes|means that)\b", re.IGNORECASE
)
_XREFS_RX = re.compile(
    r"\b(?:see (?:chapter|section|figure|table|page)s?|chapter \d+|section \d+(?:\.\d+)?"
    r"|figure \d+|table \d+)\b",
    re.IGNORECASE,
)
_HEDGING_RX = re.compile(
    r"\b(?:in this chapter|we will|you will learn|note that|for example|in summary"
    r"|recall that|as we saw)\b",
    re.IGNORECASE,
)
# A row of two or more column-like gaps reads as a table line.
_TABLES_RX = re.compile(r"\S(?: {2,}|\t)\S+(?: {2,}|\t)\S+")
# Index/glossary headings, or a dot-leader line into a page number.
_INDEX_GLOSSARY_RX = re.compile(
    r"^(?:index|glossary)\s*$|^\S[^\n]{0,100}\.{3,} ?\d+$", re.IGNORECASE | re.MULTILINE
)

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "did",
        "for",
        "from",
        "had",
        "has",
        "have",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "not",
        "of",
        "on",
        "or",
        "our",
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
        "those",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)


def zero_signals() -> dict[str, float]:
    """The zeroed signal map (one key per weighted signal)."""
    return dict.fromkeys(SCAN_WEIGHTS, 0.0)


def measure_signals(texts: list[str], outline_items: int) -> dict[str, float]:
    """The eight signals over the sampled page texts."""
    text_layer = (
        sum(1 for text in texts if len(text) >= MIN_TEXT_CHARS) / len(texts) if texts else 0.0
    )
    signals = {
        "text_layer": round(text_layer, 3),
        "outline": 1.0 if outline_items > 0 else 0.0,
        "overlap": round(neighbor_overlap(texts), 3),
    }
    for name, regex in (
        ("definitions", _DEFINITIONS_RX),
        ("xrefs", _XREFS_RX),
        ("hedging", _HEDGING_RX),
        ("tables", _TABLES_RX),
        ("index_glossary", _INDEX_GLOSSARY_RX),
    ):
        cap = SIGNAL_CAPS[name]
        mean = sum(len(regex.findall(text)) for text in texts) / len(texts) if texts else 0.0
        signals[name] = round(min(1.0, mean / cap), 3)
    return signals


def neighbor_overlap(texts: list[str]) -> float:
    """Mean token-set Jaccard between consecutive sampled pages."""
    token_sets = [top_tokens(text) for text in texts]
    pairs = [(first, second) for first, second in pairwise(token_sets) if first and second]
    if not pairs:
        return 0.0
    scores = [len(a & b) / len(a | b) for a, b in pairs]
    return sum(scores) / len(scores)


def top_tokens(text: str, count: int = TOP_TOKENS) -> frozenset[str]:
    """The page's most frequent non-stopword tokens; ties break by word."""
    words = re.findall(r"[a-z]{3,}", text.lower())
    seen: dict[str, int] = {}
    for word in words:
        if word not in _STOPWORDS:
            seen[word] = seen.get(word, 0) + 1
    ranked = sorted(seen, key=lambda w: (-seen[w], w))
    return frozenset(ranked[:count])
