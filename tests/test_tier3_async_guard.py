"""The tier-3 NLI judge seam must fail loud on an unwrapped async judge.

The live judge (nli_client.nli_mutual_entailment) is async while
NLIJudge = Callable[[str, str], bool] is sync; passing the async one
unwrapped makes every pair's coroutine object truthy - every escalated unit
would silently rate semantic entropy 0. The guard turns that into a
TypeError at the integration seam.
"""

from __future__ import annotations

import pytest

from corpus_kb.coding.uncertainty.tier3_consistency import mutual_entailment_pairs


async def _async_judge(_a: str, _b: str) -> bool:
    return True


def test_async_judge_raises_type_error() -> None:
    with pytest.raises(TypeError, match="coroutine"):
        mutual_entailment_pairs(["rationale one", "rationale two"], _async_judge)


def test_sync_judge_still_passes_through() -> None:
    pairs = mutual_entailment_pairs(["rationale one", "rationale two"], lambda a, b: True)
    assert pairs == [(0, 1)]
