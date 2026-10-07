"""Live NLI path on the already-required Qwen3 model (todo 16).

requires_ollama: CI skips this; locally it proves the fixed-prompt
temperature-0 call through the real Ollama service. The offline fixture
contract lives in tests/test_tier3_consistency.py.
"""

from __future__ import annotations

import os

import pytest

from corpus_kb.coding.uncertainty.nli_client import nli_mutual_entailment
from corpus_kb.coding.uncertainty.tier3_consistency import self_consistency

pytestmark = pytest.mark.requires_ollama

_MODEL = os.environ.get("CORPUS_KB_NLI_MODEL", "qwen3:4b")

_A = "The participant says outright that they never used the reporting feature at all."
_B = "They deny ever having opened the reporting feature during the whole period."
_C = "The speaker focuses on the subscription being too expensive, and says that is why they quit."


@pytest.mark.asyncio
async def test_live_nli_separates_paraphrase_from_contrast():
    entail = await nli_mutual_entailment(_A, _B, model=_MODEL)
    contradict = await nli_mutual_entailment(_A, _C, model=_MODEL)
    assert entail is True
    assert contradict is False


def test_live_self_consistency_clusters_recorded_shape():
    import asyncio

    rationales = [
        _A,
        _B,
        _C,
        "They report that they did not use the reporting feature, plain and simple.",
    ]
    judgments: list[bool | None] = []

    def sync_judge(a: str, b: str) -> bool:
        result = asyncio.run(nli_mutual_entailment(a, b, model=_MODEL))
        judgments.append(result)
        return bool(result)

    result = self_consistency(rationales, sync_judge)
    assert result.n_nli_calls == 6
    assert None not in judgments
    assert result.n_clusters == 2
