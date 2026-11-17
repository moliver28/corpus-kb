"""Vector math for the embedder boundary.

Moved from ``embedder.py`` (issue #468) to hold that file at its size
ceiling. The contracts are byte-identical to the versions they replace:

- ``l2_normalize``: L2-normalize; a ZERO vector (the degraded-mode signal
  from a failed embed call) stays all-zero rather than dividing by zero
  (house zero-vector fallback contract, todo 13 r7/r8).
- ``assert_unit_or_zero``: unit-norm boundary assert that no-ops on zero
  vectors; any other non-unit output is a boundary bug and raises.
- ``_slice_normalize``: front-slice to ``dim`` and L2-renormalize
  (matryoshka truncation); zero vectors pass through unchanged.

``embedder.py`` re-exports these names, so the stable public import path
``corpus_kb.rag.embedder`` keeps working for callers and tests.
"""

from __future__ import annotations

import math

# Boundary normalization tolerance (todo 13, r7): every research vector must
# be L2-normalized where the embedder emits it; zero vectors (degraded mode)
# are exempt so the house no-Ollama fallback keeps passing.
UNIT_NORM_TOLERANCE = 0.02


def l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector at the embedder boundary.

    A ZERO vector (the degraded-mode signal from a failed embed call) stays
    all-zero rather than dividing by zero — house zero-vector fallback
    contract (todo 13, r7/r8).
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def assert_unit_or_zero(vec: list[float]) -> None:
    """Unit-norm boundary assert that NO-OPS on zero vectors.

    Degraded/no-Ollama runs emit zero vectors and must skip-pass (house
    contract); any other non-unit output is a boundary bug and raises.
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return
    if abs(norm - 1.0) > UNIT_NORM_TOLERANCE:
        raise ValueError(f"expected unit-norm vector, got norm {norm:.6f}")


def _slice_normalize(vec: list[float], dim: int) -> list[float]:
    """Front-slice to `dim` dimensions and L2-renormalize (matryoshka truncation).

    A zero vector (the degraded-mode signal from a failed embed call) stays
    all-zero rather than dividing by zero.
    """
    sliced = vec[:dim]
    norm = sum(x * x for x in sliced) ** 0.5
    if norm == 0.0:
        return sliced
    return [x / norm for x in sliced]
