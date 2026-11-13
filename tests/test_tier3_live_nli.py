"""Live NLI smoke on the already-required Qwen3 model (todo 16).

requires_ollama: skips when Ollama is down, the exact NLI model tag is not
pulled, or the model does not answer one short generate call (a stuck
server used to pass the old tags-only probe, then every call burned
NLI_TIMEOUT_SECONDS and the tests failed — issue #357).

The borderline judgment pairs live offline as recorded judgments in
tests/fixtures/research/nli_recorded_qwen3.json (real model builds flip
them, issue #323); tests/test_tier3_consistency.py pins their recorded
shape. This module keeps ONE live smoke whose verdicts do not depend on
the model build.
"""

from __future__ import annotations

import asyncio
import functools
import os

import pytest

from corpus_kb.coding.uncertainty.nli_client import nli_mutual_entailment
from corpus_kb.coding.uncertainty.tier3_consistency import self_consistency

pytestmark = pytest.mark.requires_ollama

_MODEL = os.environ.get("CORPUS_KB_NLI_MODEL", "qwen3:4b")
_OLLAMA_BASE = "http://localhost:11434"

# Unambiguous smoke pairs. The entail pair is a same-facts paraphrase; the
# contrast pair states disjoint facts. Both stayed constant across repeated
# calls on this build, so the smoke cannot flip on judgment taste.
_SMOKE_ENTAIL = ("The report contains three errors.", "The report has three errors.")
_SMOKE_CONTRAST = ("The meeting starts at nine.", "The meeting was cancelled.")


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
    from tests.live_services import loopback_probe_status

    # The guard keeps the probe on the local dev server; None means skip.
    status = loopback_probe_status(
        _OLLAMA_BASE,
        "/api/generate",
        method="POST",
        payload={
            "model": _MODEL,
            "prompt": "Reply with one word: ENTAIL",
            "stream": False,
            "think": False,
            "options": {"num_predict": 4},
        },
        timeout=30,
    )
    return status == 200


def test_live_nli_smoke_judges_and_clusters():
    if not _model_available():
        pytest.skip(
            f"Ollama has no responsive {_MODEL}. "
            "The recorded fixture tests in test_tier3_consistency.py cover the contract."
        )
    entail = asyncio.run(nli_mutual_entailment(*_SMOKE_ENTAIL, model=_MODEL))
    assert entail is True
    contradict = asyncio.run(nli_mutual_entailment(*_SMOKE_CONTRAST, model=_MODEL))
    assert contradict is False

    judgments: list[bool | None] = []

    def sync_judge(a: str, b: str) -> bool:
        result = asyncio.run(nli_mutual_entailment(a, b, model=_MODEL))
        judgments.append(result)
        return bool(result)

    result = self_consistency([*_SMOKE_ENTAIL], sync_judge)
    assert result.n_nli_calls == 1
    assert judgments == [True]
    assert result.n_clusters == 1
