"""MCP/HTTP/socket/CLI handler for the active-learning op (AMR program).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern:

  * :func:`active_learn_next` — rank the tenant's STORED
    ``chunk_predictions`` for the next human-labeling batch. Three
    strategies: ``margin`` (least confident first), ``entropy``
    (highest binary entropy of the stored probability), and
    ``random_incumbent`` (seeded 42, so a noisy-borderline bias can
    never hide inside the pick).

UNCERTAINTY PROXY (the honest-scoring note): ``chunk_predictions``
stores the WINNING probability only, so the true top-2 margin is not
recoverable here. ``margin`` ranks by ``1 - p``; for a binary label set
this orders exactly like the true ``p1 - p2`` gap, and ``entropy`` is
the binary entropy of the same probability. The note rides every
success envelope so a reader never mistakes the proxy for the gap.

Determinism: ties break on ``chunk_id`` ascending; the random strategy
draws from one ``random.Random(42)`` stream in the stored row order, so
the same stored rows give the same ranked batch on every call.
"""

from __future__ import annotations

import math
import random
from typing import cast

import asyncpg

from ..classification import store
from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["STRATEGIES", "active_learn_next"]

STRATEGIES = ("margin", "entropy", "random_incumbent")

RANDOM_SEED = 42

# The bounded prediction window (the read/list bounds convention; the
# ranker needs the whole pool, so the cap is a memory guard, not a sample).
MAX_PREDICTION_WINDOW = 100_000

_ROUND = 6

_STRATEGY_REMEDY = f"Use one of the supported strategies: {', '.join(STRATEGIES)}."
_BATCH_REMEDY = "batch must be a positive whole number."
_UNAVAILABLE_REASON = "classification tables absent (migration 029 not applied)"
_UNAVAILABLE_REMEDY = "run corpus-kb setup"
_EMPTY_REASON = "no stored predictions; run classification-build with a seed file first."
_EMPTY_REMEDY = "Run classification-build with a seed file, then retry."
_PROXIMITY_NOTE = (
    "chunk_predictions stores the winning probability only. The margin score is "
    "1 - p; for a binary set it orders exactly like the true top-2 gap. Entropy "
    "is the binary entropy of the same probability."
)


def _honest(status: str, reason: str, remedy: str) -> dict[str, object]:
    """The honest no-op envelope on the op keys (reason + remedy named)."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
        "ranked": [],
        "strategy": "margin",
        "requested_batch": 10,
    }


def _margin_score(probability: float) -> float:
    """1 - p: low confidence first (the binary top-2 gap order)."""
    return round(1.0 - probability, _ROUND)


def _entropy_score(probability: float) -> float:
    """Binary entropy of the stored probability; 0.0 at the extremes."""
    probability = min(max(probability, 0.0), 1.0)
    if probability in (0.0, 1.0):
        return 0.0
    value = -(
        probability * math.log(probability) + (1.0 - probability) * math.log(1.0 - probability)
    )
    return round(value, _ROUND)


def _scored_rows(rows: list[dict[str, object]], strategy: str) -> list[dict[str, object]]:
    """Attach the strategy score to every stored prediction row."""
    rng = random.Random(RANDOM_SEED)
    scored: list[dict[str, object]] = []
    for row in rows:
        probability = min(max(float(cast("float", row["probability"])), 0.0), 1.0)
        if strategy == "margin":
            score = _margin_score(probability)
        elif strategy == "entropy":
            score = _entropy_score(probability)
        else:
            score = round(rng.random(), _ROUND)
        scored.append(
            {
                "chunk_id": str(row["chunk_id"]),
                "set_id": str(row["set_id"]),
                "label": str(row["label"]),
                "probability": round(probability, _ROUND),
                "review": bool(row["review"]),
                "score": score,
            }
        )
    return scored


async def active_learn_next(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    strategy: str = "margin",
    batch: int = 10,
) -> dict[str, object]:
    """Rank stored predictions for the next labeling batch.

    Returns ``{status, ranked, strategy, requested_batch}`` plus
    reason/remedy on the honest guard paths and the scoring note on
    success. ``ranked`` rows carry chunk_id, set_id, label, probability,
    review, and the strategy score; ties break on chunk_id ascending.
    """
    del config  # accepted for MCP-tool signature symmetry; reads are config-free
    if strategy not in STRATEGIES:
        raise ValidationError(
            f"strategy {strategy!r} is not supported. Supported: {', '.join(STRATEGIES)}.",
            remediation=_STRATEGY_REMEDY,
        )
    if isinstance(batch, bool) or not isinstance(batch, int) or batch <= 0:
        raise ValidationError(
            f"batch must be a positive int; got {batch!r}.", remediation=_BATCH_REMEDY
        )
    if not await store.classification_tables_present(pg_pool):
        return _honest("unavailable", _UNAVAILABLE_REASON, _UNAVAILABLE_REMEDY)
    rows = await store.read_predictions(pg_pool, tenant_id, MAX_PREDICTION_WINDOW)
    if not rows:
        return _honest("empty", _EMPTY_REASON, _EMPTY_REMEDY)
    scored = _scored_rows(rows, strategy)
    scored.sort(key=lambda row: (-float(cast("float", row["score"])), str(row["chunk_id"])))
    ranked = scored[: max(1, batch)]
    return {
        "status": "success",
        "ranked": ranked,
        "strategy": strategy,
        "requested_batch": batch,
        "pool_size": len(scored),
        "scoring_note": _PROXIMITY_NOTE,
    }
