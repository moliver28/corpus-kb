"""Live coder-model client: the one network call in the coding pipeline.

Split out of coder_window.py, which is otherwise pure text/data transforms
(contract text, window rendering, output types, validator, decision parser).
Keeping the network call here means coder_window.py imports nothing that can
touch a socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _generate(window: str, model: str, base_url: str) -> tuple[Any, Any]:
    """Blocking Ollama generate, returning (raw_text, logprobs).

    Runs on a worker thread (see call_ollama): the ollama client is fully
    synchronous, so calling it inline would stall the ASGI event loop for the
    whole generation and block every other in-flight request.
    """
    from ollama import Client

    response = Client(host=base_url).generate(
        model=model,
        prompt=window,
        stream=False,
        think=False,
        logprobs=True,
        top_logprobs=5,
        options={"temperature": 0},
    )
    raw = getattr(response, "response", None)
    logprobs = getattr(response, "logprobs", None)
    if isinstance(response, dict):
        raw = raw if raw is not None else response.get("response")
        logprobs = logprobs if logprobs is not None else response.get("logprobs")
    return raw, logprobs


async def call_ollama(
    window: str, model: str, base_url: str = "http://localhost:11434"
) -> dict[str, Any]:
    """Live local-model call with native logprobs, off the event loop.

    No live call is ever made under test: dispatch_chunk tests inject `call_fn`,
    and the degrade paths below are covered with a monkeypatched ollama client.
    Request shape follows docs/coding/spike-logprobs.md; the response object is
    typed by the ollama client, so `.response` / `.logprobs` are read via getattr
    with a dict fallback.

    Degrade contract (mirrors OllamaJudge in src/rag/judge.py): never raises into
    the caller. A transport failure or a non-JSON reply returns
    {"error": "..."} , which dispatch_chunk turns into a clean rejection instead
    of an exception escaping mid-dispatch.
    """
    from ollama import ResponseError

    try:
        raw, logprobs = await asyncio.to_thread(_generate, window, model, base_url)

        parsed = json.loads(str(raw))
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError("coder reply is not a JSON object", str(raw), 0)
    except (
        ConnectionError,
        OSError,
        httpx.NetworkError,
        ResponseError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning("Coder model call unavailable: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}

    parsed["logprobs"] = [
        lp if isinstance(lp, dict) else lp.model_dump() for lp in (logprobs or [])
    ] or None
    return parsed
