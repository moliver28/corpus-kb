"""Shared tier signal dataclasses and constants."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from corpus_kb.coding.uncertainty.tier2_hedging import hedging_score


@dataclass
class TierSignals:
    """Uncertainty signals from tiers 0-2.

    Tier 0: Multi-prototype similarity (cosine distance to k-medoid exemplars).
    Tier 1: Token-logprob entropy from LLM response (None if logprobs unavailable).
    Tier 2: Verbalized hedging (regex count of epistemic markers).
    """

    tier0_sim: float
    tier1_entropy: float | None
    tier2_hedges: int
    fired: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize to JSON for chunk_codes.tier_signals column."""
        return json.dumps(asdict(self))


def assemble_tier_signals(
    tier0: float | None, entropy: float | None, rationale: str, quote_status: str
) -> TierSignals:
    """Assemble the per-(chunk, code) tier signals and their `fired` markers.

    `quote_failed` fires when the verbatim gate could not find the model's quote
    in the chunk, so a fabricated quote stays auditable in the append-only
    chunk_codes row independently of the `route` column.
    """
    fired = [
        "tier0_missing" if tier0 is None else "tier0",
        "tier1_unavailable" if entropy is None else "tier1",
    ]
    hedges = hedging_score(rationale)
    if hedges:
        fired.append("tier2")
    if quote_status == "not_found":
        fired.append("quote_failed")
    return TierSignals(
        tier0_sim=float(tier0 or 0.0),
        tier1_entropy=entropy,
        tier2_hedges=hedges,
        fired=fired,
    )
