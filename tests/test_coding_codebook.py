"""Tests for codebook.py theory grounding (propose_theory)."""

from __future__ import annotations

import asyncio

from corpus_kb.coding.codebook import THEORY_PROMPT, propose_theory


def test_theory_prompt_interpolation_tolerates_json_braces() -> None:
    """The prompt's literal JSON example must not be read as format fields."""
    code = {
        "brief_definition": "pause > 2 seconds",
        "inclusion_criteria": "inc",
        "exclusion_criteria": "exc",
    }

    async def fake_call(prompt: str) -> dict[str, object]:
        return {}

    result = asyncio.run(propose_theory(code, fake_call))

    assert result == {"tradition": None, "construct": None, "citation": None, "note": None}


def test_theory_prompt_carries_definition_into_request() -> None:
    captured: list[str] = []

    async def fake_call(prompt: str) -> dict[str, object]:
        captured.append(prompt)
        return {}

    code = {
        "brief_definition": "pause > 2 seconds",
        "inclusion_criteria": "inc",
        "exclusion_criteria": "exc",
    }
    asyncio.run(propose_theory(code, fake_call))

    assert captured
    assert "pause > 2 seconds" in captured[0]
    assert '{"tradition": null' in captured[0]
    assert "{definition}" not in captured[0]
    assert "{inclusion}" not in captured[0]
    assert "{exclusion}" not in captured[0]
    assert THEORY_PROMPT.startswith("You are a qualitative researcher")
