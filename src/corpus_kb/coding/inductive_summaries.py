"""Atomic-observation summaries (todo 15, v5 §9.1).

Every coding unit is summarized to ONE atomic observation at temperature 0,
with the prompt id and model logged on the stored row. For interviews the
answer is summarized in the context of its question and the observation
records whether the meaning is ``question_dependent`` (v5 §9.1) — the flag
feeds question-dependent routing downstream (todo 16's w6 signal).

The LLM is injectable (anything with an async ``chat`` returning Ollama's
response dict) so tests run offline. A malformed model response degrades to
the raw answer text with ``parse_fallback`` recorded — summaries are never
allowed to crash a run (house degraded-mode contract).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

SUMMARY_PROMPT_ID = "inductive.atomic_observation.v1"
SUMMARY_TEMPERATURE = 0.0

SUMMARY_SYSTEM_PROMPT = (
    "You summarize qualitative-research answers into single atomic "
    "observations for thematic clustering. Respond with STRICT JSON only: "
    '{"summary": "<one sentence, <=40 words, self-contained>", '
    '"question_dependent": <true|false>}. '
    "question_dependent is true when the answer's meaning cannot be "
    "understood without its question (e.g. 'yes, that fixed it')."
)


class SummaryChatClient(Protocol):
    """Minimal LLM surface the summarizer needs (LlmHandler satisfies it)."""

    model: str

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        options: dict[str, object] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ObservationSummary:
    """One atomic observation with its provenance (logged, never inferred)."""

    unit_id: int
    summary: str
    question_dependent: bool
    prompt_id: str
    model: str
    temperature: float
    parse_fallback: bool


def build_summary_messages(question: str, answer: str) -> list[dict[str, str]]:
    """Question-context-aware chat messages (v5 §9.1)."""
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question asked by the interviewer:\n{question}\n\n"
                f"Participant answer:\n{answer}\n\n"
                "Return the JSON observation now."
            ),
        },
    ]


def parse_observation(raw: str, model: str, unit_id: int) -> ObservationSummary:
    """Parse the model JSON; degrade to the raw answer text on failure."""
    candidate = _json_object(raw)
    if candidate is not None:
        summary = str(candidate.get("summary", "")).strip()
        if summary:
            return ObservationSummary(
                unit_id=unit_id,
                summary=summary,
                question_dependent=bool(candidate.get("question_dependent", False)),
                prompt_id=SUMMARY_PROMPT_ID,
                model=model,
                temperature=SUMMARY_TEMPERATURE,
                parse_fallback=False,
            )
    return ObservationSummary(
        unit_id=unit_id,
        summary=_fallback_summary(raw),
        question_dependent=False,
        prompt_id=SUMMARY_PROMPT_ID,
        model=model,
        temperature=SUMMARY_TEMPERATURE,
        parse_fallback=True,
    )


def _json_object(raw: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a (possibly chatty) response."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _fallback_summary(raw: str) -> str:
    """Degraded observation: the answer text itself, truncated."""
    text = " ".join(raw.split())
    if not text:
        text = "(empty answer)"
    return text[:400]


async def summarize_unit(
    client: SummaryChatClient,
    unit_id: int,
    question: str,
    answer: str,
) -> ObservationSummary:
    """One temp-0 LLM call -> atomic observation (question-context aware)."""
    response = await client.chat(
        build_summary_messages(question, answer),
        options={"temperature": SUMMARY_TEMPERATURE},
    )
    if "error" in response:
        return ObservationSummary(
            unit_id=unit_id,
            summary=_fallback_summary(answer),
            question_dependent=False,
            prompt_id=SUMMARY_PROMPT_ID,
            model=str(response.get("model", client.model)),
            temperature=SUMMARY_TEMPERATURE,
            parse_fallback=True,
        )
    content = ""
    message = response.get("message")
    if isinstance(message, dict):
        content = str(message.get("content", ""))
    elif isinstance(response.get("response"), str):
        content = str(response["response"])
    return parse_observation(content, str(response.get("model", client.model)), unit_id)


META_PROMPT_ID = "inductive.existing_vs_new.v1"

META_DECISIONS = ("existing", "new", "unclear")


@dataclass(frozen=True)
class MetaDecision:
    """One high-entropy unit's 'existing code vs new code' verdict (v5 §9.4)."""

    unit_id: int
    decision: str
    prompt_id: str
    model: str


def build_meta_messages(
    question: str, answer: str, existing_code_names: list[str]
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You adjudicate whether a qualitative answer is covered by an "
                "EXISTING code or evidences a NEW code. Respond with STRICT "
                'JSON only: {"decision": "existing"|"new"|"unclear"}.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Existing codes: {existing_code_names or '(none)'}\n\n"
                f"Question:\n{question}\n\nAnswer:\n{answer}\n\n"
                "Return the JSON decision now."
            ),
        },
    ]


async def meta_decide(
    client: SummaryChatClient,
    unit_id: int,
    question: str,
    answer: str,
    existing_code_names: list[str],
) -> MetaDecision:
    """LLM meta-decision for high-entropy units ONLY (Tier-0 gate upstream)."""
    response = await client.chat(
        build_meta_messages(question, answer, existing_code_names),
        options={"temperature": SUMMARY_TEMPERATURE},
    )
    model = str(response.get("model", client.model))
    decision = "unclear"
    if "error" not in response:
        message = response.get("message")
        content = (
            str(message.get("content", ""))
            if isinstance(message, dict)
            else str(response.get("response", ""))
        )
        parsed = _json_object(content)
        candidate = str(parsed.get("decision", "")).strip().lower() if parsed else ""
        if candidate in META_DECISIONS:
            decision = candidate
    return MetaDecision(unit_id=unit_id, decision=decision, prompt_id=META_PROMPT_ID, model=model)
