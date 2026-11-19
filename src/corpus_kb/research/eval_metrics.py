"""Retrieval-eval metric math (split from ``retrieval_eval.py``).

Pure, label-type agnostic ranking metrics: recall@k, MRR, nDCG@10, and the
unit-id extractor over arm payloads. One TypeVar keeps both callers — the
U45 arms scoring unit ids and the eval harness scoring deterministic
chunk-id UUIDs — on THESE functions (no reimplementation).
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, TypeVar

import numpy as np

_L = TypeVar("_L", bound=Hashable)

__all__ = ["_L", "_mrr", "_ndcg", "_ranked_unit_ids", "_recall_k"]


def _recall_k(ranked: list[_L], relevant: frozenset[_L], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def _mrr(ranked: list[_L], relevant: frozenset[_L]) -> float:
    for pos, unit_id in enumerate(ranked, start=1):
        if unit_id in relevant:
            return 1.0 / pos
    return 0.0


def _ndcg(ranked: list[_L], relevant: frozenset[_L], k: int = 10) -> float:
    if not relevant:
        return 0.0
    dcg = sum(
        1.0 / np.log2(pos + 2) for pos, unit_id in enumerate(ranked[:k]) if unit_id in relevant
    )
    ideal = sum(1.0 / np.log2(pos + 2) for pos in range(min(len(relevant), k)))
    return float(dcg / ideal) if ideal > 0 else 0.0


def _ranked_unit_ids(payloads: list[dict[str, Any]]) -> list[int]:
    return [int(p["_unit_id"]) for p in payloads if p.get("_unit_id") is not None]
