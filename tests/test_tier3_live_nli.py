"""Live NLI path on the already-required Qwen3 model (todo 16).

requires_ollama: skips when Ollama is down, the exact NLI model tag is not
pulled, or the model does not answer one short generate call (a stuck
server used to pass the old tags-only probe, then every call burned
NLI_TIMEOUT_SECONDS and the tests failed — issue #357).

The offline fixture contract lives in tests/test_tier3_consistency.py.
"""

from __future__ import annotations

import functools
import json
import os
import urllib.request

import pytest

from corpus_kb.coding.uncertainty.nli_client import nli_mutual_entailment
from corpus_kb.coding.uncertainty.tier3_consistency import self_consistency

pytestmark = pytest.mark.requires_ollama

_MODEL = os.environ.get("CORPUS_KB_NLI_MODEL", "qwen3:4b")

_A = "The participant says outright that they never used the reporting feature at all."
_B = "They deny ever having opened the reporting feature during the whole period."
_C = "The speaker focuses on the subscription being too expensive, and says that is why they quit."


@functools.lru_cache(maxsize=1)
def _model_available() -> bool:
    """True only when the exact NLI tag answers one short generate call.

    The old probe read /api/tags and accepted any tag with the same base
    name, so qwen3:8b passed for qwen3:4b and the real call 404-ed; a
    stuck server passed too. This probe asks the exact model for a capped
    reply (30 s) and retries once for a cold model load.
    """
    return any(_one_generate_answers() for _ in range(2))


def _one_generate_answers() -> bool:
    body = json.dumps(
        {
            "model": _MODEL,
            "prompt": "Reply with one word: ENTAIL",
            "stream": False,
            "think": False,
            "options": {"num_predict": 4},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200
    except OSError:
        return False


@pytest.mark.asyncio
async def test_live_nli_separates_paraphrase_from_contrast():
    if not _model_available():
        pytest.skip(f"Ollama has no responsive {_MODEL}; the live NLI tests skip.")
    entail = await nli_mutual_entailment(_A, _B, model=_MODEL)
    contradict = await nli_mutual_entailment(_A, _C, model=_MODEL)
    assert entail is True
    assert contradict is False


def test_live_self_consistency_clusters_recorded_shape():
    import asyncio

    if not _model_available():
        pytest.skip(f"Ollama has no responsive {_MODEL}; the live NLI tests skip.")
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
