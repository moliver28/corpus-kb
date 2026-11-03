"""Offline unit tests for the tier-3 rationale resampler (issue #132).

The live Ollama call is a thin wrapper; everything around it — prompt
rendering, reply cleaning, and the degrade loop — is covered here, so the
diff-coverage gate sees the module's logic without any network.
"""

from __future__ import annotations

import pytest

from corpus_kb.coding.uncertainty.resample_client import (
    _clean,
    _extract_raw,
    build_resample_prompt,
    resample_rationales,
)


def test_build_resample_prompt_renders_the_fixed_template() -> None:
    prompt = build_resample_prompt("unit text", "code-a", "its definition")
    assert 'Code: "code-a" - its definition' in prompt
    assert 'Unit text: "unit text"' in prompt
    assert prompt.endswith("/no_think")


def test_clean_strips_think_block_and_squeezes_whitespace() -> None:
    assert _clean("<think>hidden</think>\n  the  rationale  ") == "the rationale"
    assert _clean("") == ""


def test_extract_raw_reads_object_and_dict_responses() -> None:
    class Reply:
        response = "from attr"

    assert _extract_raw(Reply()) == "from attr"
    assert _extract_raw({"response": "from dict"}) == "from dict"
    assert _extract_raw({"response": None, "other": "x"}) == "None"


async def test_resample_rationales_collect_seeded_samples(monkeypatch) -> None:
    import corpus_kb.coding.uncertainty.resample_client as resample_client

    monkeypatch.setattr(
        resample_client, "_generate", lambda prompt, model, base_url, seed: f"r{seed}"
    )
    out = await resample_rationales("t", "c", "d", 3, "m", "http://x")
    assert out == ["r0", "r1", "r2"]


async def test_resample_rationales_drop_failures_and_empties(monkeypatch) -> None:
    import corpus_kb.coding.uncertainty.resample_client as resample_client

    def flaky(prompt: str, model: str, base_url: str, seed: int) -> str:
        if seed == 1:
            raise ConnectionError("endpoint down")
        if seed == 2:
            return "<think>x</think>   "
        return f"r{seed}"

    monkeypatch.setattr(resample_client, "_generate", flaky)
    out = await resample_rationales("t", "c", "d", 4, "m", "http://x")
    assert out == ["r0", "r3"], "failed and unusable samples drop; usable ones keep order"


async def test_resample_rationales_all_failed_is_not_measured(monkeypatch) -> None:
    import corpus_kb.coding.uncertainty.resample_client as resample_client

    def down(prompt: str, model: str, base_url: str, seed: int) -> str:
        raise ConnectionError("endpoint down")

    monkeypatch.setattr(resample_client, "_generate", down)
    assert await resample_rationales("t", "c", "d", 2, "m", "http://x") == []


@pytest.mark.parametrize("bad", [OSError("socket"), TimeoutError("timed out")])
async def test_resample_rationales_degrade_on_os_errors(monkeypatch, bad: Exception) -> None:
    import corpus_kb.coding.uncertainty.resample_client as resample_client

    def raising(prompt: str, model: str, base_url: str, seed: int) -> str:
        raise bad

    monkeypatch.setattr(resample_client, "_generate", raising)
    assert await resample_rationales("t", "c", "d", 2, "m", "http://x") == []
