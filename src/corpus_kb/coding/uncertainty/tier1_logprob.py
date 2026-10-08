"""Token-logprob entropy from Ollama native /api/generate endpoint.

Captures uncertainty in the LLM's token choices via Shannon entropy over the
top_logprobs distribution. When Ollama logprobs are unavailable, returns None
as a degrade marker for the confidence router.
"""

from __future__ import annotations

import math
from typing import Any


def logprob_entropy(token_logprobs: list[Any]) -> float | None:
    """Mean per-token Shannon entropy from Ollama top_logprobs.

    Args:
        token_logprobs: list of logprob records from Ollama /api/generate,
            each with a "top_logprobs" list of {"token": ..., "logprob": ...}.
            Typically 3-5 tokens per position when logprobs enabled.

    Returns:
        Mean Shannon entropy across all tokens: 0.0 = one token dominates
        (certainty), higher = more uniform (theoretical max ln(k) per
        position, ~1.61 for top-5 logprobs - NOT normalized to 1.0).
        None if payload lacks logprobs (degrade marker).
    """
    if not token_logprobs:
        return None

    entropies = []
    for token_record in token_logprobs:
        top_logprobs = token_record.get("top_logprobs", [])
        if not top_logprobs:
            continue

        # Convert logprobs to probabilities: p = exp(logp)
        # Normalize so they sum to 1 (accounting for missing tail)
        logprobs = [t.get("logprob", 0.0) for t in top_logprobs]
        if not logprobs:
            continue

        # exp(logp) -> prob, handling underflow
        max_logp = max(logprobs)
        probs = [math.exp(lp - max_logp) for lp in logprobs]
        total = sum(probs)
        if total > 0:
            probs = [p / total for p in probs]
        else:
            continue

        # Shannon entropy: -sum(p * log(p))
        h = 0.0
        for p in probs:
            if p > 1e-10:
                h -= p * math.log(p)
        entropies.append(h)

    if not entropies:
        return None

    return sum(entropies) / len(entropies)
