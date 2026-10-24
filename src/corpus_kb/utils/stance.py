"""Shared stance vocabulary (Living Corpus hardening).

ONE word list for stance detection, used by the conflict cascade
(Gate B relation swaps) and the belief rebuild (evidence stances) —
the two lists drifted historically, so "I can't stand X" was a Gate-B
conflict yet stance-free for beliefs. Single words match their bare and
plural surface forms; multiword phrases match verbatim. Matching is
whitespace-delimited containment on the lowercased text.
"""

from __future__ import annotations

POSITIVE_WORDS: tuple[str, ...] = ("like", "love", "prefer", "enjoy", "support", "adore")
NEGATIVE_WORDS: tuple[str, ...] = (
    "dislike",
    "hate",
    "avoid",
    "oppose",
    "cannot stand",
    "can't stand",
)


def stance_words(text: str) -> set[str]:
    """The stance polarities present in the text: {"pos", "neg"} subsets."""
    low = " ".join(text.lower().split())
    found: set[str] = set()
    for polarity, words in (("pos", POSITIVE_WORDS), ("neg", NEGATIVE_WORDS)):
        for word in words:
            if " " in word:
                if f" {word} " in f" {low} ":
                    found.add(polarity)
                continue
            if f" {word} " in f" {low} " or f" {word}s " in f" {low} ":
                found.add(polarity)
    return found


def stance_of(text: str) -> str | None:
    """'positive' / 'negative' when exactly one polarity is present."""
    found = stance_words(text)
    if found == {"pos"}:
        return "positive"
    if found == {"neg"}:
        return "negative"
    return None
