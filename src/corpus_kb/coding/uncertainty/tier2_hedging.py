"""Verbalized-hedging epistemic marker counter.

Detects uncertainty cues in LLM rationales via compiled regex over a hedging
lexicon. Used to flag low-confidence assignments within a confidence band.
"""

from __future__ import annotations

import re

# Compiled regex for common hedging markers
_HEDGING_PATTERN = re.compile(
    r"\b(?:"
    r"possibly|probably|might|may|maybe|seems|appears|arguably|perhaps|"
    r"somewhat|relatively|apparently|allegedly|purportedly|"
    r"could|can|uncertain|unclear|ambiguous|"
    r"roughly|approximately|around|about|sort of|kind of|"
    r"rather|in a sense|in some sense|"
    r"suggests|implies|indicates|points to|tends|"
    r"likely|unlikely|doubtful|questionable"
    r")\b",
    re.IGNORECASE,
)


def hedging_score(text: str) -> int:
    """Count hedging markers in text.

    Args:
        text: LLM rationale or decision text

    Returns:
        Number of epistemic hedges found (may|might|possibly|etc.).
    """
    return len(_HEDGING_PATTERN.findall(text))
