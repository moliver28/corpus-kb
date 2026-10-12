"""Fixed-prompt temperature-0 NLI call on the already-required Qwen3 model.

One judgment per unordered rationale pair: the FIXED prompt below asks for
bidirectional (mutual) entailment — does each description entail the other —
which is exactly the relation Kuhn et al. (arXiv:2302.09664) use to decide
semantic equivalence for entropy clustering. N=5 resamples => 10 calls per
escalated unit.

Degrade contract mirrors coder_client.call_ollama: never raises into the
caller; a transport failure or unparseable reply yields None (treated as
non-entailing downstream and recorded as a degrade in the run manifest).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

NLI_MODEL = "qwen3"
NLI_TEMPERATURE = 0

# Bound on every generate call (seconds). The ollama client otherwise waits
# forever: a wedged server accepts TCP but never answers the POST, which hung
# the whole pytest suite when Ollama died between the reachability probe and
# the generate (2026-10-09). 120s covers a cold qwen3:4b load on CPU; slower
# than that must DEGRADE (None) per the contract below, never hang.
NLI_TIMEOUT_SECONDS = 120.0

# FIXED prompt (nli_prompt_id pins its sha256 in the run manifest — r11
# reproducibility). Single-word answer keeps parsing deterministic; the
# qwen3 /no_think soft switch keeps the temperature-0 judgment short.
NLI_PROMPT_TEMPLATE = (
    "You are a natural language inference judge. Two analysts independently "
    "described the same observation.\n"
    'Description A: "{a}"\n'
    'Description B: "{b}"\n'
    "Mutual entailment means A entails B AND B entails A — they state the "
    "same meaning, allowing wording differences. Reply with exactly one "
    "word: ENTAIL if they are mutually entailing, otherwise CONTRADICT."
    "\n/no_think"
)

NLI_PROMPT_ID = hashlib.sha256(NLI_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:16]

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_JUDGMENT = re.compile(r"\b(ENTAIL|CONTRADICT)\b", re.IGNORECASE)


def build_nli_prompt(a: str, b: str) -> str:
    """Render the fixed prompt for one ordered description pair."""
    return NLI_PROMPT_TEMPLATE.format(a=a, b=b)


def parse_nli_reply(raw: str) -> bool | None:
    """Parse the one-word judgment; None when the reply is unusable.

    Tolerates a leaked reasoning block: any ``<think>...</think>`` span is
    removed first, then the LAST judgment token in the remainder wins (the
    model's final word is the answer; earlier mentions are prompt echoes).
    """
    body = _THINK_BLOCK.sub(" ", str(raw))
    matches = list(_JUDGMENT.finditer(body))
    if not matches:
        return None
    return matches[-1].group(1).upper() == "ENTAIL"


def _generate(prompt: str, model: str, base_url: str) -> str:
    """Blocking Ollama generate at temperature 0 (worker thread).

    The explicit timeout is load-bearing: without it a server that accepts
    the connection but never responds blocks the worker thread forever.
    """
    from ollama import Client

    response = Client(host=base_url, timeout=NLI_TIMEOUT_SECONDS).generate(
        model=model,
        prompt=prompt,
        stream=False,
        think=False,
        options={"temperature": NLI_TEMPERATURE, "seed": 0},
    )
    raw = getattr(response, "response", None)
    if isinstance(response, dict):
        raw = raw if raw is not None else response.get("response")
    return str(raw)


async def nli_mutual_entailment(
    a: str, b: str, model: str = NLI_MODEL, base_url: str = "http://localhost:11434"
) -> bool | None:
    """Bidirectional-entailment judgment for one pair; None on any failure.

    No live call is made under test: tests inject fixture judgments into
    tier3_consistency.self_consistency; this live path is exercised only by
    requires_ollama tests.
    """
    from ollama import ResponseError

    try:
        raw = await asyncio.to_thread(_generate, build_nli_prompt(a, b), model, base_url)
        return parse_nli_reply(raw)
    # TransportError is the common base of NetworkError, TimeoutException and
    # ProtocolError (incl. RemoteProtocolError: a wedged server may accept the
    # socket then disconnect without responding, which CI's linux runners hit).
    # Any of these must degrade to None exactly like a refused connection
    # (builtin TimeoutError is an OSError subclass and is already covered).
    except (
        ConnectionError,
        OSError,
        httpx.TransportError,
        ResponseError,
    ) as exc:
        logger.warning("NLI model call unavailable: %s", exc)
        return None


def fixture_judge(records: list[dict[str, Any]]) -> Any:
    """Build an offline NLIJudge from recorded fixture records (CI, no network).

    Each record: {"a": str, "b": str, "entails": bool, "raw": str}. The judge
    is symmetric; unknown pairs raise so fixtures cannot silently drift.
    """
    table: dict[tuple[str, str], bool] = {}
    for rec in records:
        a, b = str(rec["a"]), str(rec["b"])
        entails = bool(rec["entails"])
        table[(a, b)] = entails
        table[(b, a)] = entails

    def judge(x: str, y: str) -> bool:
        key = (x, y)
        if key not in table:
            raise KeyError(f"no recorded NLI judgment for pair: {key!r}")
        return table[key]

    return judge


def fixture_records_to_json(records: list[dict[str, Any]]) -> str:
    """Serialize recorded judgments (fixture capture format)."""
    return json.dumps(
        {"nli_prompt_id": NLI_PROMPT_ID, "model": NLI_MODEL, "records": records},
        indent=2,
    )
