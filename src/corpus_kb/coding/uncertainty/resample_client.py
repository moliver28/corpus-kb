"""Fixed-prompt rationale resampling for tier-3 escalation (issue #132).

The escalated minority of a deductive run gets N resampled coding
rationales. The prompt is FIXED per unit; only the seed varies, so the
samples stay same-distribution (Oracle A r7). This is the live path the
cascade consumes through ``run_deductive``; tests inject fakes at that
call seam and never reach this module.

Degrade contract mirrors ``nli_client``: never raises into the caller.
A failed sample is dropped; an empty return tells the cascade the unit
is NOT MEASURED, so no semantic entropy is fabricated.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re

import httpx

logger = logging.getLogger(__name__)

RESAMPLE_TEMPERATURE = 0.7

# Same bound rationale as nli_client: a wedged server must degrade, never
# hang the worker thread.
RESAMPLE_TIMEOUT_SECONDS = 120.0

# FIXED prompt (the id pins its sha256 in run metadata for reproducibility).
# The /no_think soft switch keeps the temperature-0.7 sample short.
RESAMPLE_PROMPT_TEMPLATE = (
    "You are a qualitative coder. Restate, in one short sentence, the "
    "rationale for applying the code below to the unit.\n"
    'Code: "{code}" - {definition}\n'
    'Unit text: "{text}"\n'
    "Answer with the rationale sentence only.\n/no_think"
)

RESAMPLE_PROMPT_ID = hashlib.sha256(RESAMPLE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:16]

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def build_resample_prompt(text: str, code_name: str, definition: str) -> str:
    """Render the fixed prompt for one unit under one code."""
    return RESAMPLE_PROMPT_TEMPLATE.format(code=code_name, definition=definition, text=text)


def _clean(raw: object) -> str:
    """Strip a leaked reasoning block and squeeze whitespace.

    None means NO rationale (a null/missing reply field on the degrade
    path): the honest result is '' — the caller drops the sample — never
    the truthy string ``"None"`` passed off as a rationale.
    """
    if raw is None:
        return ""
    body = _THINK_BLOCK.sub(" ", str(raw))
    return " ".join(body.split())


def _generate(prompt: str, model: str, base_url: str, seed: int) -> str:
    """Blocking Ollama generate with one seed (worker thread)."""
    from ollama import Client

    response = Client(host=base_url, timeout=RESAMPLE_TIMEOUT_SECONDS).generate(
        model=model,
        prompt=prompt,
        stream=False,
        think=False,
        options={"temperature": RESAMPLE_TEMPERATURE, "seed": seed},
    )
    return _extract_raw(response)


def _extract_raw(response: object) -> str:
    """The reply text out of an ollama response (object or dict shape)."""
    raw = getattr(response, "response", None)
    if isinstance(response, dict):
        raw = raw if raw is not None else response.get("response")
    return _clean(raw)


async def resample_rationales(
    text: str,
    code_name: str,
    definition: str,
    n: int,
    model: str,
    base_url: str,
) -> list[str]:
    """N resampled rationale strings; fewer when samples fail or come back empty.

    Args:
        text: The unit text under coding.
        code_name: The applied code name.
        definition: The applied code definition.
        n: Requested sample count (seeded 0..n-1).
        model: The resampling model tag.
        base_url: The Ollama endpoint.

    Returns:
        The usable rationale strings in seed order. Empty means NOT MEASURED.
    """
    from ollama import ResponseError

    prompt = build_resample_prompt(text, code_name, definition)
    out: list[str] = []
    for seed in range(n):
        try:
            raw = await asyncio.to_thread(_generate, prompt, model, base_url, seed)
        except (ConnectionError, OSError, httpx.TransportError, ResponseError) as exc:
            logger.warning("resample %s/%s unavailable: %s", seed + 1, n, exc)
            continue
        rationale = _clean(raw)
        if rationale:
            out.append(rationale)
    return out
