"""Rung ladder (TD-06, R15): cheapest rung first, band rail, human last.

Order: local small, local medium, cloud small, cloud medium, cloud large,
then a human. A site skips rungs it does not configure: local rungs come
from ``models.tiers``, cloud rungs need a model in ``coding.cloud.models``
AND ``coding.cloud.consent`` true. ``coding.tier`` picks the first rung
(``small`` starts at local small; ``medium``/``frontier`` start at local
medium — the rungs are positions, the tier block is the model source).
Per unit: rung k codes it; grade A stops; else the next rung recodes it
with the same prompt version (render digest equal); no rung left goes to
the human queue. Rail (C4): the escalation share stays inside the 10-20
percent band (``cascade`` owns the constants); outside it a mature run
halts at ``cycle_halted`` — no silent cost drift. When more than half
the units leave the first rung, the first rung moves up one step (C12).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from corpus_kb.coding.uncertainty.cascade import ESCALATION_BAND

__all__ = [
    "LADDER_ORDER",
    "MIN_CODED_FOR_BAND",
    "RUNG_HUMAN",
    "band_halt",
    "configured_rungs",
    "first_rung",
    "first_rung_moveup",
    "next_rung",
]

RUNG_LOCAL_SMALL = "local_small"
RUNG_LOCAL_MEDIUM = "local_medium"
RUNG_CLOUD_SMALL = "cloud_small"
RUNG_CLOUD_MEDIUM = "cloud_medium"
RUNG_CLOUD_LARGE = "cloud_large"
RUNG_HUMAN = "human"

LADDER_ORDER: tuple[str, ...] = (
    RUNG_LOCAL_SMALL,
    RUNG_LOCAL_MEDIUM,
    RUNG_CLOUD_SMALL,
    RUNG_CLOUD_MEDIUM,
    RUNG_CLOUD_LARGE,
    RUNG_HUMAN,
)

#: The band check needs at least this many coded units before a halt
#: fires (owned convention, C12: below it the share is noise, so the run
#: reports instead of halting).
MIN_CODED_FOR_BAND = 10

_CLOUD_MODEL_KEYS = {
    RUNG_CLOUD_SMALL: "small",
    RUNG_CLOUD_MEDIUM: "medium",
    RUNG_CLOUD_LARGE: "large",
}


def configured_rungs(config: Mapping[str, Any]) -> list[str]:
    """The ladder rungs this site can run, in ladder order.

    Local rungs need their ``models.tiers`` row (small, medium/frontier
    both resolve local models); cloud rungs need a model name and consent.
    """
    models = config.get("models")
    tiers = models.get("tiers") if isinstance(models, Mapping) else None
    coding = config.get("coding")
    coding = coding if isinstance(coding, Mapping) else {}
    cloud = coding.get("cloud")
    cloud = cloud if isinstance(cloud, Mapping) else {}
    cloud_models = cloud.get("models")
    cloud_models = cloud_models if isinstance(cloud_models, Mapping) else {}
    consent = cloud.get("consent") is True

    def _configured(rung: str) -> bool:
        if rung == RUNG_LOCAL_SMALL:
            return isinstance(tiers, Mapping) and "small" in tiers
        if rung == RUNG_LOCAL_MEDIUM:
            return isinstance(tiers, Mapping) and ("medium" in tiers or "frontier" in tiers)
        if rung in _CLOUD_MODEL_KEYS:
            return consent and bool(cloud_models.get(_CLOUD_MODEL_KEYS[rung]))
        return True  # the human rung is always configured

    return [rung for rung in LADDER_ORDER if _configured(rung)]


def first_rung(rungs: list[str], tier: str) -> str:
    """The rung ``coding.tier`` starts from, honoring the site's rungs.

    ``small`` starts at local small; ``medium`` and ``frontier`` both
    start at local medium (the tier block supplies the model either way).
    """
    if tier == "small" and RUNG_LOCAL_SMALL in rungs:
        return RUNG_LOCAL_SMALL
    if RUNG_LOCAL_MEDIUM in rungs:
        return RUNG_LOCAL_MEDIUM
    return rungs[0] if rungs else RUNG_HUMAN


def next_rung(current: str, rungs: list[str]) -> str | None:
    """The next configured rung after ``current``, or None (human queue)."""
    if current == RUNG_HUMAN:
        return None
    position = LADDER_ORDER.index(current)
    for rung in rungs:
        if LADDER_ORDER.index(rung) > position:
            return rung
    return None


def band_halt(escalated: int, coded: int) -> tuple[bool, str]:
    """The 10-20 percent rail: (halt, reason); the reason is empty inside it.

    Below ``MIN_CODED_FOR_BAND`` coded units the share is noise, so no
    halt fires and the reason says why (honest insufficient-data shape).
    """
    if coded < MIN_CODED_FOR_BAND:
        note = f"escalation share not checked: {coded} coded units is below {MIN_CODED_FOR_BAND}"
        return False, note
    share = escalated / coded if coded else 0.0
    low, high = ESCALATION_BAND
    if low <= share <= high:
        return False, ""
    reason = (
        f"cycle_halted: escalation share {share:.2f} sits outside the "
        f"{low:.2f}-{high:.2f} band; a human decides before the run continues"
    )
    return True, reason


def first_rung_moveup(left_first: int, total: int) -> bool:
    """True when more than half the units left the first rung (TD-06)."""
    return total > 0 and left_first / total > 0.5
