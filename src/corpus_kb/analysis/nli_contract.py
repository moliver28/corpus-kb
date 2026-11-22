"""The locked NLI contract for the zero-shot ops (AMR program).

Prompt wording, verdict vocabulary, and label iteration order are
LOCKED here because zero-shot NLI verdicts swing on them (the ACL 2021
sensitivity rule). Never permute, never paraphrase. Judgments run at
temperature 0 through ``coding.uncertainty.nli_client`` — the one
qwen3 generate path — and transport failures degrade to a typed
internal error the handlers translate into honest ``unavailable``
envelopes.

Verdict to score mapping: ENTAIL 2, NEUTRAL 1, CONTRADICT 0.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

_LOG = logging.getLogger(__name__)

__all__ = [
    "NLI_BASE_URL",
    "NLI_HYPOTHESIS_TEMPLATE",
    "VERDICT_SCORES",
    "NliError",
    "build_batch_classification_prompt",
    "build_classification_prompt",
    "build_contradiction_prompt",
    "build_hypothesis",
    "judge_batch_verdicts",
    "judge_raw",
    "judge_verdict",
    "parse_batch_verdicts",
    "parse_verdict",
]

NLI_BASE_URL = "http://localhost:11434"

# The locked zero-shot hypothesis (wording changes swing verdicts).
NLI_HYPOTHESIS_TEMPLATE = "This text is about {label}."

# Verdict words and their entailment scores (higher is more entailed).
VERDICT_SCORES: dict[str, float] = {"ENTAIL": 2.0, "NEUTRAL": 1.0, "CONTRADICT": 0.0}

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_VERDICT = re.compile(r"\b(ENTAIL|NEUTRAL|CONTRADICT)\b", re.IGNORECASE)

_CLASSIFY_PROMPT = (
    "You are a natural language inference judge. Read one text.\n"
    'Text: "{premise}"\n'
    'Hypothesis: "{hypothesis}"\n'
    "Reply with exactly one word: ENTAIL if the hypothesis is true of the "
    "text, NEUTRAL if the text does not say, CONTRADICT if the hypothesis "
    "is false.\n/no_think"
)

_BATCH_PROMPT = (
    "You are a natural language inference judge. Read one text.\n"
    'Text: "{premise}"\n'
    "Judge each numbered hypothesis below.\n"
    "{numbered}\n"
    "Reply with ONLY a JSON list, one object per hypothesis, in order: "
    '[{{"n": 1, "verdict": "..."}}, ...]. '
    "Each verdict is one word: ENTAIL if the hypothesis is true of the "
    "text, NEUTRAL if the text does not say, CONTRADICT if the hypothesis "
    "is false.\n/no_think"
)

_ENTRY_ROW = re.compile(
    r'\{\s*"n"\s*:\s*(\d+)\s*,\s*"verdict"\s*:\s*"(ENTAIL|NEUTRAL|CONTRADICT)"\s*\}',
    re.IGNORECASE,
)

_CONTRADICTION_PROMPT = (
    "You are a natural language inference judge. Compare two texts from one "
    "corpus.\n"
    'Text A: "{a}"\n'
    'Text B: "{b}"\n'
    "Contradiction means the two texts state claims that cannot both be true.\n"
    "Reply with exactly one word: CONTRADICT if they contradict, otherwise "
    "NEUTRAL.\n/no_think"
)


class NliError(Exception):
    """One judgment could not be made; ``kind`` is unreachable|unusable."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def build_hypothesis(label: str) -> str:
    """The locked hypothesis sentence for one label."""
    return NLI_HYPOTHESIS_TEMPLATE.format(label=label)


def build_classification_prompt(premise: str, label: str) -> str:
    """The locked premise/hypothesis judgment prompt (bytes pinned)."""
    return _CLASSIFY_PROMPT.format(premise=premise, hypothesis=build_hypothesis(label))


def build_contradiction_prompt(a: str, b: str) -> str:
    """The locked two-text contradiction prompt (bytes pinned)."""
    return _CONTRADICTION_PROMPT.format(a=a, b=b)


def build_batch_classification_prompt(premise: str, statements: dict[str, str]) -> str:
    """The locked one-text, many-hypothesis judgment prompt.

    Labels arrive as ``{label: hypothesis}``; order follows the dict, so
    callers pass a deterministic order. One call replaces one call per
    label (issue #538).
    """
    numbered = "\n".join(
        f"{index}. {statement}" for index, statement in enumerate(statements.values(), 1)
    )
    return _BATCH_PROMPT.format(premise=premise, numbered=numbered)


def parse_batch_verdicts(raw: str, statements: dict[str, str]) -> dict[str, str | None]:
    """Map each label to its verdict; a missing row maps to ``None``.

    The reply must be a JSON list whose objects carry the statement
    number as ``"n"`` and a verdict word. A leaked reasoning block is
    stripped first; when JSON parsing fails, the entries are recovered
    with a strict per-row pattern. ``None`` means "no honest verdict
    for this label" — never a guess.
    """
    body = _THINK_BLOCK.sub(" ", str(raw))
    body = body.replace("</think>", " ")
    try:
        data = json.loads(body)
        rows = data if isinstance(data, list) else []
    except json.JSONDecodeError:
        # The list may ride after plain reasoning text; recover it.
        rows = []
        start = body.find("[")
        end = body.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(body[start : end + 1])
                rows = data if isinstance(data, list) else []
            except json.JSONDecodeError:
                rows = []
        if not rows:
            rows = [
                {"n": match.group(1), "verdict": match.group(2)}
                for match in _ENTRY_ROW.finditer(body)
            ]
    by_number: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_number = row.get("n")
        if isinstance(raw_number, bool) or not isinstance(raw_number, (int, str)):
            continue
        try:
            number = int(raw_number)
        except ValueError:
            continue
        verdict = str(row.get("verdict", "")).upper()
        if verdict in VERDICT_SCORES:
            if number in by_number:
                _LOG.debug(
                    "Duplicate NLI row for statement %d; the last verdict wins.",
                    number,
                )
            by_number[number] = verdict
    return {label: by_number.get(number) for number, label in enumerate(statements, 1)}


async def judge_batch_verdicts(
    prompt: str, statements: dict[str, str], model: str, base_url: str
) -> dict[str, str]:
    """One call judges every statement; raises ``NliError`` on any degrade.

    A transport failure raises ``unreachable``; a reply that lacks a
    verdict for any label raises ``unusable`` — the caller answers the
    honest unavailable envelope instead of guessing.
    """
    errors: tuple[type[BaseException], ...] = (ConnectionError, OSError, httpx.TransportError)
    try:  # the transport error family of the ollama client (lazy, P0)
        from ollama import ResponseError

        errors = (*errors, ResponseError)
    except ImportError:
        pass
    try:
        raw = await judge_raw(prompt, model, base_url)
    except errors as exc:
        raise NliError("unreachable", f"the NLI model call failed: {exc}") from exc
    parsed = parse_batch_verdicts(raw, statements)
    missing = [label for label, verdict in parsed.items() if verdict is None]
    if missing:
        raise NliError(
            "unusable",
            f"the batch reply had no verdict for: {', '.join(missing)}.",
        )
    return {label: str(verdict) for label, verdict in parsed.items()}


def parse_verdict(raw: str) -> str | None:
    """The LAST verdict word wins; None when the reply is unusable.

    Tolerates a leaked reasoning block: any ``<think>...</think>`` span
    is removed first (the nli_client parse rule, widened to NEUTRAL).
    """
    body = _THINK_BLOCK.sub(" ", str(raw))
    matches = list(_VERDICT.finditer(body))
    if not matches:
        return None
    return matches[-1].group(1).upper()


async def judge_raw(prompt: str, model: str, base_url: str) -> str:
    """The transport seam (tests substitute recorded fixtures here)."""
    from corpus_kb.coding.uncertainty import nli_client

    raw = await asyncio.to_thread(nli_client._generate, prompt, model, base_url)
    return str(raw)


async def judge_verdict(prompt: str, model: str, base_url: str) -> str:
    """One model judgment; raises ``NliError`` on any degrade."""
    errors: tuple[type[BaseException], ...] = (ConnectionError, OSError, httpx.TransportError)
    try:  # the transport error family of the ollama client (lazy, P0)
        from ollama import ResponseError

        errors = (*errors, ResponseError)
    except ImportError:
        pass
    try:
        raw = await judge_raw(prompt, model, base_url)
    except errors as exc:
        raise NliError("unreachable", f"the NLI model call failed: {exc}") from exc
    verdict = parse_verdict(raw)
    if verdict is None:
        raise NliError("unusable", "the model reply had no verdict word.")
    return verdict
