"""Offline tests for atomic-observation summaries + meta-decision (todo 15)."""

from __future__ import annotations

import pytest

from corpus_kb.coding.inductive_summaries import (
    META_PROMPT_ID,
    build_meta_messages,
    build_summary_messages,
    meta_decide,
    parse_observation,
    summarize_unit,
)


class FakeChat:
    """Records calls; returns a canned Ollama-shaped chat response."""

    model = "fake-model"

    def __init__(self, content: str, *, error: str | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        options: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.calls.append({"messages": messages, "options": options})
        if self.error is not None:
            return {"error": self.error, "model": self.model}
        return {
            "model": self.model,
            "message": {"role": "assistant", "content": self.content},
        }


@pytest.mark.asyncio
async def test_summarize_unit_calls_at_temperature_zero_with_context():
    client = FakeChat(
        '{"summary": "Participant adopted the tool after onboarding.", "question_dependent": false}'
    )
    obs = await summarize_unit(client, 7, "What changed after onboarding?", "We switched.")
    assert obs.unit_id == 7
    assert obs.summary.startswith("Participant adopted")
    assert obs.question_dependent is False
    assert obs.parse_fallback is False
    assert obs.prompt_id == "inductive.atomic_observation.v1"
    assert obs.model == "fake-model"
    assert obs.temperature == 0.0
    assert client.calls[0]["options"] == {"temperature": 0.0}
    messages = client.calls[0]["messages"]
    assert isinstance(messages, list)
    assert "What changed after onboarding?" in str(messages[1]["content"])
    assert "We switched." in str(messages[1]["content"])


@pytest.mark.asyncio
async def test_summarize_unit_records_question_dependent_flag():
    client = FakeChat(
        '```json\n{"summary": "Yes, that fixed it.", "question_dependent": true}\n```'
    )
    obs = await summarize_unit(client, 1, "Did the patch help?", "Yes, that fixed it.")
    assert obs.question_dependent is True
    assert obs.parse_fallback is False


@pytest.mark.asyncio
async def test_summarize_unit_degrades_on_malformed_json():
    client = FakeChat("I think the answer is about pricing concerns overall.")
    obs = await summarize_unit(client, 2, "Q", "Pricing is the main concern.")
    assert obs.parse_fallback is True
    assert obs.question_dependent is False
    assert "pricing" in obs.summary.lower()


@pytest.mark.asyncio
async def test_summarize_unit_degrades_on_llm_error():
    client = FakeChat("", error="connection refused")
    obs = await summarize_unit(client, 3, "Q", "The raw answer text.")
    assert obs.parse_fallback is True
    assert obs.summary.startswith("The raw answer text.")
    assert obs.model == "fake-model"


def test_build_summary_messages_shapes_are_stable():
    messages = build_summary_messages("Q text", "A text")
    assert messages[0]["role"] == "system"
    assert "question_dependent" in messages[0]["content"]
    assert messages[1]["role"] == "user"


def test_parse_observation_json_object_extraction():
    obs = parse_observation('noise {"summary": "s", "question_dependent": true} trailing', "m", 9)
    assert obs.summary == "s"
    assert obs.question_dependent is True


@pytest.mark.asyncio
async def test_meta_decide_parses_new():
    client = FakeChat('{"decision": "new"}')
    decision = await meta_decide(client, 5, "Q", "A", ["existing_code"])
    assert decision.decision == "new"
    assert decision.prompt_id == META_PROMPT_ID
    assert decision.model == "fake-model"
    messages = client.calls[0]["messages"]
    assert isinstance(messages, list)
    assert "existing_code" in str(messages[1]["content"])


@pytest.mark.asyncio
async def test_meta_decide_degrades_to_unclear():
    client = FakeChat("cannot say")
    decision = await meta_decide(client, 6, "Q", "A", [])
    assert decision.decision == "unclear"


def test_meta_messages_include_existing_codes():
    messages = build_meta_messages("Q", "A", ["code_a", "code_b"])
    assert "code_a" in messages[1]["content"]
