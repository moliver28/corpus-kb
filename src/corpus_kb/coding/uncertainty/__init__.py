"""Uncertainty tiers 0-3 for coding confidence + review routing (todo 16).

Tier 3 (self-consistency) and the combined review-priority router extend the
tier 0-2 stack; tiers 4/5 are deferred entirely (DeferredTierError guard).
"""

from __future__ import annotations

from corpus_kb.coding.uncertainty.tier3_consistency import (
    MAX_SUPPORTED_TIER,
    DeferredTierError,
    require_supported_tier,
)

__all__ = [
    "MAX_SUPPORTED_TIER",
    "DeferredTierError",
    "require_supported_tier",
]
