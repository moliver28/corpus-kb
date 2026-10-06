"""G1 promotion mechanics (todo 13, r5): explicit config, never silent writes.

Promotion of a G1 winner is an EXPLICIT COMMITTED config change — the
default ``research.embedder`` block in ``config.yaml`` gains the winner —
never a runtime write. The only automated guard: only 1024-emitting models
are PROMOTABLE (migration-016 vector strategy: vector(1024) primary, never
padded). A non-1024 winner HALTS promotion for an explicit config +
migration change.
"""

from __future__ import annotations

PROMOTABLE_DIMENSIONS = 1024


class NonPromotableDimensionsError(RuntimeError):
    """A G1 winner that does not emit 1024 dims halts promotion."""


def assert_promotable(dimensions: int) -> None:
    """HALT unless the embedder emits exactly 1024 dims (016 contract)."""
    if dimensions != PROMOTABLE_DIMENSIONS:
        raise NonPromotableDimensionsError(
            f"G1 winner emits {dimensions} dims; only {PROMOTABLE_DIMENSIONS}-emitting "
            "models are promotable — halt for an explicit config + migration change "
            "(never silent padding, migration-016 vector strategy)"
        )
