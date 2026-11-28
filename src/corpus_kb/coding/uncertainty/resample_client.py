"""Fixed-prompt rationale resampling for tier-3 escalation (issue #132).

The escalated minority gets N resampled rationales; the prompt is FIXED per
unit, only the seed varies (Oracle A r7). TAO T1 (TD-03): the prompt lives in
``coding/prompts/resample.yaml`` (registry render). Degrade contract mirrors
``nli_client``: never raises; a failed sample is dropped; an empty return
says NOT MEASURED, so no semantic entropy is fabricated.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from corpus_kb.coding.prompt_registry import render_prompt

logger = logging.getLogger(__name__)

RESAMPLE_TEMPERATURE = 0.7

# Same bound rationale as nli_client: a wedged server must degrade, never
# hang the worker thread.
RESAMPLE_TIMEOUT_SECONDS = 120.0

# Registry prompt id + version (the id@version pins reproducibility in run
# metadata; the render digest covers the variable values per call).
RESAMPLE_PROMPT_ID = "resample@1.0.0"

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def build_resample_prompt(text: str, code_name: str, definition: str) -> str:
    """Render the fixed prompt for one unit under one code."""
    rendered = render_prompt(
        "resample", {"code": code_name, "definition": definition, "text": text}
    )
    return rendered.rendered.rstrip("\n")


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
