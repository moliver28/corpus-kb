"""QA context chaining for participant turns.

When a participant is answering a moderator question, the moderator's question
provides critical context that improves answer classification. This module pools
similarity signals (MAX of plain answer similarity and context-aware similarity)
and chains the moderator question with the participant answer for pooling.
"""

from __future__ import annotations


def pool_score(plain_sim: float, ctx_sim: float) -> float:
    """Pool plain and context-aware similarity scores.

    For a candidate chunk, both plain text similarity and context-augmented
    (chained question + answer) similarity are computed. The pool score takes
    the maximum of the two, reflecting the best-case match under either framing.

    Args:
        plain_sim: Similarity of the answer text alone.
        ctx_sim: Similarity of the chained (question + answer) text.

    Returns:
        Maximum of the two similarity scores.
    """
    return max(plain_sim, ctx_sim)


def chain_context(role: str, moderator_q: str, answer: str) -> str:
    """Chain moderator question with participant answer when applicable.

    For a participant turn (customer answer), prepend the moderator's question
    to provide context. For a moderator turn, return the answer unchanged.

    Args:
        role: Speaker role ("moderator", "participant", "unknown").
        moderator_q: The preceding moderator question.
        answer: The candidate chunk text (participant answer).

    Returns:
        Chained text (question + ' ' + answer) for participant turns,
        answer alone otherwise.
    """
    if role == "participant":
        return moderator_q + " " + answer
    return answer
