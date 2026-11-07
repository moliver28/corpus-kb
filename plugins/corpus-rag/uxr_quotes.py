"""Quote checking for the UXR layer (issue #355).

Classes: ``exact``, ``near_miss``, ``fail``. Normalization is Unicode
NFKC, then lowercase, then every punctuation character to a space, then
whitespace runs collapse to one space. A near miss is a ``difflib``
ratio of 0.90 or more between the normalized quote and a sliding word
window of the normalized unit text.

Stdlib only.
"""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher

EXACT = "exact"
NEAR_MISS = "near_miss"
FAIL = "fail"
NEAR_MISS_THRESHOLD = 0.90
WINDOW_SLACK = 2


def normalize(text: str) -> str:
    """NFKC, lowercase, punctuation to space, whitespace runs to one."""
    folded = unicodedata.normalize("NFKC", text).lower()
    out: list[str] = []
    for char in folded:
        if char.isalnum():
            out.append(char)
        elif char.isspace():
            out.append(" ")
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def check_quote(quote: str, unit_text: str, threshold: float = NEAR_MISS_THRESHOLD) -> str:
    """The quote class against one unit of text."""
    nq = normalize(quote)
    nt = normalize(unit_text)
    if not nq or not nt:
        return FAIL
    if nq in nt:
        return EXACT
    if _best_ratio(nq, nt) >= threshold:
        return NEAR_MISS
    return FAIL


def _best_ratio(nq: str, nt: str) -> float:
    """The best window ratio; windows carry up to WINDOW_SLACK extra words."""
    quote_words = nq.split()
    unit_words = nt.split()
    if not quote_words or len(quote_words) > len(unit_words):
        return 0.0
    best = 0.0
    # SequenceMatcher caches its index for seq2, so the stable quote sits
    # in seq2 and each window goes through the cheap set_seq1.
    matcher = SequenceMatcher(None, "", nq)
    for slack in range(WINDOW_SLACK + 1):
        size = len(quote_words) + slack
        if size > len(unit_words):
            break
        for start in range(len(unit_words) - size + 1):
            matcher.set_seq1(" ".join(unit_words[start : start + size]))
            ratio = matcher.ratio()
            if ratio > best:
                best = ratio
                if best >= 0.999:
                    return best
    return best
