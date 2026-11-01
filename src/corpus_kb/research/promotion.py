"""G1 promotion mechanics (todo 13, r5): explicit config, never silent writes.

Promotion of a G1 winner is an EXPLICIT COMMITTED config change — the
default ``research.embedder`` block in ``config.yaml`` gains the winner —
never a runtime write. The only automated guard: only 1024-emitting models
are PROMOTABLE (migration-016 vector strategy: vector(1024) primary, never
padded). A non-1024 winner HALTS promotion for an explicit config +
migration change.
"""

from __future__ import annotations

from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS

PROMOTABLE_DIMENSIONS = RESEARCH_EMBEDDING_DIMENSIONS


class NonPromotableDimensionsError(RuntimeError):
    """A G1 winner that does not emit 1024 dims halts promotion."""


def assert_promotable(dimensions: int) -> None:
    """HALT unless the embedder emits exactly 1024 dims (016 contract)."""
    if dimensions != PROMOTABLE_DIMENSIONS:
        raise NonPromotableDimensionsError(
            f"G1 winner emits {dimensions} dims. Only models that emit "
            f"{PROMOTABLE_DIMENSIONS} dims are promotable. Promotion halts "
            "until you change the config and the migration explicitly. "
            "Never pad silently (migration-016 vector strategy)."
        )
