"""The llm arm of the keyphrases op: one fixed temperature-0 call.

Mirrors ``coding/uncertainty/nli_client.py``: fixed qwen3 model,
temperature 0, seed 0, hard timeout, ``/no_think`` short answer, lazy
``ollama`` import (the P0 rule), and honest degrade envelopes — a
transport failure answers ``unavailable``, an unparseable reply answers
``empty``. The model gives the ORDER of the phrases, never scores: rows
carry 0.0 in model order (the honest-envelope note below).

The envelope builder (:func:`honest_envelope`) lives here too, so the
tfidf/embedding arms in ``keyphrase_tools.py`` and this arm share one
envelope shape on every non-success path.
"""

from __future__ import annotations

import asyncio
import json
import re

__all__ = ["honest_envelope", "llm_variant", "parse_llm_keyphrases"]

# Bounded text per call (the honest-latency guard).
MAX_CHUNK_CHARS = 1200
MAX_TOTAL_CHARS = 6000

# The call pins: fixed model, temp 0, hard timeout (the nli_client rule).
LLM_MODEL = "qwen3"
LLM_TEMPERATURE = 0
LLM_TIMEOUT_SECONDS = 120.0
LLM_BASE_URL = "http://localhost:11434"

KEYPHRASE_PROMPT = (
    "You are a keyphrase extractor.\n"
    "Read the text. Pick the {limit} most important keyphrases.\n"
    "Keyphrases are short noun phrases from the text.\n"
    "Reply with one JSON list of strings. No other text.\n\n"
    "Text:\n{text}\n\n/no_think"
)

_LLM_ORDER_NOTE = "The model gives the order, not scores. Rows carry 0.0 in model order."
_PARSE_FAIL_REASON = "the model reply did not hold a JSON list of phrases."
_PARSE_FAIL_REMEDY = "Retry the call, or use the tfidf variant."
_UNREACHABLE_REMEDY = "Start Ollama and pull the model, then retry."

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_LIST = re.compile(r"\[.*\]", re.DOTALL)


def honest_envelope(
    status: str, reason: str, remedy: str, variant: str, screened: int
) -> dict[str, object]:
    """The honest no-op envelope on the op keys (reason + remedy named)."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
        "keyphrases": [],
        "variant": variant,
        "screened_count": screened,
    }


def _generate(prompt: str, model: str, base_url: str) -> str:
    """Blocking Ollama generate at temperature 0 (worker thread)."""
    from ollama import Client

    response = Client(host=base_url, timeout=LLM_TIMEOUT_SECONDS).generate(
        model=model,
        prompt=prompt,
        stream=False,
        think=False,
        options={"temperature": LLM_TEMPERATURE, "seed": 0},
    )
    raw = getattr(response, "response", None)
    if isinstance(response, dict):
        raw = raw if raw is not None else response.get("response")
    return str(raw)


def parse_llm_keyphrases(raw: str, limit: int) -> list[str] | None:
    """The reply's JSON list of phrases, deduped and capped; None when unusable."""
    body = _THINK_BLOCK.sub(" ", str(raw))
    match = _JSON_LIST.search(body)
    if match is None:
        return None
    try:
        parsed: object = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(parsed, list):
        return None
    phrases: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        phrase = item.strip()
        if phrase and phrase not in phrases:
            phrases.append(phrase)
        if len(phrases) >= limit:
            break
    return phrases


async def llm_variant(texts: list[str], limit: int, screened: int) -> dict[str, object]:
    """One temperature-0 call; honest empty on a parse failure."""
    import httpx

    joined = " ".join(text[:MAX_CHUNK_CHARS] for text in texts)[:MAX_TOTAL_CHARS]
    prompt = KEYPHRASE_PROMPT.format(limit=limit, text=joined)
    try:
        raw = await asyncio.to_thread(_generate, prompt, LLM_MODEL, LLM_BASE_URL)
    except (ConnectionError, OSError, httpx.TransportError) as exc:
        return honest_envelope(
            "unavailable",
            f"the keyphrase model call failed: {exc}.",
            _UNREACHABLE_REMEDY,
            "llm",
            screened,
        )
    phrases = parse_llm_keyphrases(raw, limit)
    if phrases is None:
        return honest_envelope("empty", _PARSE_FAIL_REASON, _PARSE_FAIL_REMEDY, "llm", screened)
    return {
        "status": "success",
        "keyphrases": [{"phrase": phrase, "score": 0.0} for phrase in phrases],
        "variant": "llm",
        "screened_count": screened,
        "model": LLM_MODEL,
        "note": _LLM_ORDER_NOTE,
    }
