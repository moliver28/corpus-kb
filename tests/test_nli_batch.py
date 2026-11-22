"""Tests for the batched classification prompt (issue #538).

Covered: the 8-in-1 prompt names every hypothesis, one call judges all
labels, the structured reply parses per label, and a partial reply
(missing or malformed label rows) degrades to the honest unusable
error — no guessed labels.
"""

from __future__ import annotations

import json

import pytest

from corpus_kb.analysis import nli_contract as contract

STATEMENTS = {
    "anger": "The text expresses anger.",
    "joy": "The text expresses joy.",
    "trust": "The text expresses trust.",
}


class TestBatchPrompt:
    def test_prompt_names_every_hypothesis(self) -> None:
        prompt = contract.build_batch_classification_prompt("I love this team.", STATEMENTS)
        assert '"I love this team."' in prompt
        for statement in STATEMENTS.values():
            assert statement in prompt
        assert "ENTAIL" in prompt and "NEUTRAL" in prompt

    def test_prompt_is_deterministic(self) -> None:
        one = contract.build_batch_classification_prompt("text", STATEMENTS)
        two = contract.build_batch_classification_prompt("text", STATEMENTS)
        assert one == two

    def test_labels_are_numbered_in_given_order(self) -> None:
        prompt = contract.build_batch_classification_prompt("text", STATEMENTS)
        assert "1. " in prompt and "2. " in prompt and "3. " in prompt


class TestBatchParse:
    def _reply(self, verdicts: dict[int, str]) -> str:
        rows = [{"n": n, "verdict": v} for n, v in verdicts.items()]
        return json.dumps(rows)

    def test_full_reply_maps_every_label(self) -> None:
        raw = self._reply({1: "ENTAIL", 2: "NEUTRAL", 3: "CONTRADICT"})
        parsed = contract.parse_batch_verdicts(raw, STATEMENTS)
        assert parsed == {"anger": "ENTAIL", "joy": "NEUTRAL", "trust": "CONTRADICT"}

    def test_think_block_is_stripped(self) -> None:
        raw = "<think>hmm</think>" + self._reply({1: "ENTAIL", 2: "NEUTRAL", 3: "NEUTRAL"})
        parsed = contract.parse_batch_verdicts(raw, STATEMENTS)
        assert parsed["anger"] == "ENTAIL"

    def test_missing_label_is_none(self) -> None:
        raw = self._reply({1: "ENTAIL", 3: "NEUTRAL"})
        parsed = contract.parse_batch_verdicts(raw, STATEMENTS)
        assert parsed["anger"] == "ENTAIL"
        assert parsed["joy"] is None

    def test_garbage_reply_gives_all_none(self) -> None:
        parsed = contract.parse_batch_verdicts("I cannot answer that.", STATEMENTS)
        assert set(parsed.values()) == {None}


class TestJudgeBatch:
    async def test_one_call_returns_all_verdicts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        async def fake_raw(prompt: str, model: str, base_url: str) -> str:
            calls.append(prompt)
            return json.dumps(
                [
                    {"n": 1, "verdict": "ENTAIL"},
                    {"n": 2, "verdict": "CONTRADICT"},
                    {"n": 3, "verdict": "NEUTRAL"},
                ]
            )

        monkeypatch.setattr(contract, "judge_raw", fake_raw)
        verdicts = await contract.judge_batch_verdicts(
            contract.build_batch_classification_prompt("text", STATEMENTS),
            STATEMENTS,
            "m",
            contract.NLI_BASE_URL,
        )
        assert len(calls) == 1
        assert verdicts == {"anger": "ENTAIL", "joy": "CONTRADICT", "trust": "NEUTRAL"}

    async def test_partial_reply_raises_unusable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_raw(prompt: str, model: str, base_url: str) -> str:
            return json.dumps([{"n": 1, "verdict": "ENTAIL"}])

        monkeypatch.setattr(contract, "judge_raw", fake_raw)
        with pytest.raises(contract.NliError) as failure:
            await contract.judge_batch_verdicts("prompt", STATEMENTS, "m", "u")
        assert failure.value.kind == "unusable"

    async def test_transport_failure_raises_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def broken(prompt: str, model: str, base_url: str) -> str:
            raise ConnectionError("down")

        monkeypatch.setattr(contract, "judge_raw", broken)
        with pytest.raises(contract.NliError) as failure:
            await contract.judge_batch_verdicts("prompt", STATEMENTS, "m", "u")
        assert failure.value.kind == "unreachable"


class TestBatchParseRecovery:
    def test_reasoning_text_before_the_list_is_recovered(self) -> None:
        """A reply that reasons in plain text before the JSON still parses."""
        body = (
            "First, I read the text. Then I judge each hypothesis."
            '[{"n": 1, "verdict": "ENTAIL"}, {"n": 2, "verdict": "NEUTRAL"},'
            ' {"n": 3, "verdict": "CONTRADICT"}]'
        )
        parsed = contract.parse_batch_verdicts(body, STATEMENTS)
        assert parsed == {"anger": "ENTAIL", "joy": "NEUTRAL", "trust": "CONTRADICT"}

    def test_lone_closing_think_tag_is_stripped(self) -> None:
        rows = json.dumps([{"n": n, "verdict": "ENTAIL"} for n in range(1, 4)])
        body = f"reasoning</think>{rows}"
        parsed = contract.parse_batch_verdicts(body, STATEMENTS)
        assert set(parsed.values()) == {"ENTAIL"}

    def test_rows_recovered_by_strict_pattern(self) -> None:
        rows = json.dumps(
            [
                {"n": 1, "verdict": "ENTAIL"},
                {"n": 2, "verdict": "NEUTRAL"},
                {"n": 3, "verdict": "CONTRADICT"},
            ]
        )
        body = f"text [broken {rows}"
        parsed = contract.parse_batch_verdicts(body, STATEMENTS)
        assert parsed == {"anger": "ENTAIL", "joy": "NEUTRAL", "trust": "CONTRADICT"}
