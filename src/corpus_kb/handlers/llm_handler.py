"""LLM handler — direct Ollama HTTP access for chat and text generation.

Calls a local Ollama instance's /api/chat and /api/generate endpoints via
httpx (replaces the archived pgai path; no cloud APIs, nothing leaves the
machine).

On connection failure, non-200 status, malformed JSON, or invalid input the
handler logs a warning and returns an error dict — it never raises, so
callers can continue operating in degraded mode.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx

from ..config import load_config

logger = logging.getLogger(__name__)

# Local models load from disk on first use; a cold 4B load plus inference
# routinely passes 30 s. The claim judge already allows 120 s (rag/judge.py).
# Set llm.timeout_s (or CORPUS_KB_LLM_TIMEOUT_S) higher for slow hosts.
DEFAULT_TIMEOUT_S = 120.0


class LlmHandler:
    """Async client for a local Ollama instance's chat/generate HTTP API.

    Both methods return Ollama's parsed response dict on success, or
    ``{"error": <human-readable>, "model": <model>}`` on any failure.
    """

    def __init__(
        self,
        config: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._config = config or load_config()
        llm = cast(dict[str, object], self._config.get("llm", {}))

        self.model = _str_or_default(llm, "model", "qwen3:4b")
        self.base_url = _str_or_default(llm, "base_url", "http://localhost:11434")
        self._timeout = timeout if timeout is not None else _timeout_or_default(llm)

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        options: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """Return the chat completion for ``messages`` via /api/chat.

        ``options`` rides Ollama's native options block (e.g. temperature);
        the inductive summaries call it with temperature 0 (v5 §9.1 pin).
        """
        use_model = model or self.model
        input_error = _validate_messages(messages)
        if input_error is not None:
            logger.warning("LlmHandler.chat rejected input: %s", input_error)
            return {"error": input_error, "model": use_model}
        payload: dict[str, Any] = {"model": use_model, "messages": messages, "stream": False}
        if options:
            payload["options"] = options
        return await self._post(f"{self.base_url}/api/chat", payload, use_model)

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        options: dict[str, object] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream the chat completion for ``messages`` via /api/chat.

        Ollama answers NDJSON (one JSON object per line). Each content
        piece is yielded as ``{"message": {"content": <piece>},
        "done": False}``; the final ``done: true`` line ends the stream.
        The never-raises contract holds: any failure yields one terminal
        ``{"error": ..., "model": ...}`` event instead. Pieces may arrive
        before a failure, so a consumer must treat the error event as the
        only honest end signal.
        """
        use_model = model or self.model
        input_error = _validate_messages(messages)
        if input_error is not None:
            logger.warning("LlmHandler.chat_stream rejected input: %s", input_error)
            yield {"error": input_error, "model": use_model}
            return
        payload: dict[str, Any] = {"model": use_model, "messages": messages, "stream": True}
        if options:
            payload["options"] = options
        url = f"{self.base_url}/api/chat"
        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout) as client,
                client.stream("POST", url, json=payload) as response,
            ):
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")[:200]
                    logger.warning(
                        "Ollama returned status %s at %s: %s",
                        response.status_code,
                        url,
                        detail,
                    )
                    yield {
                        "error": f"Ollama returned status {response.status_code}: {detail}",
                        "model": use_model,
                    }
                    return
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = cast(dict[str, Any], json.loads(line))
                    except ValueError:
                        logger.warning("Ollama returned malformed JSON from %s", url)
                        yield {"error": "Ollama returned malformed JSON", "model": use_model}
                        return
                    if event.get("error"):
                        yield {"error": str(event["error"]), "model": use_model}
                        return
                    message = event.get("message")
                    piece = message.get("content", "") if isinstance(message, dict) else ""
                    if isinstance(piece, str) and piece:
                        yield {
                            "message": {"role": "assistant", "content": piece},
                            "done": False,
                        }
                    if event.get("done") is True:
                        return
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Ollama request failed at %s: %s", url, exc)
            yield {"error": f"Ollama request failed: {exc}", "model": use_model}

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Return the text generation for ``prompt`` via /api/generate."""
        use_model = model or self.model
        if not prompt.strip():
            logger.warning("LlmHandler.generate rejected input: empty prompt")
            return {"error": "prompt must be a non-empty string", "model": use_model}
        return await self._post(
            f"{self.base_url}/api/generate",
            {"model": use_model, "prompt": prompt, "stream": False},
            use_model,
        )

    async def _post(self, url: str, payload: dict[str, Any], model: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Ollama request failed at %s: %s", url, exc)
            return {"error": f"Ollama request failed: {exc}", "model": model}

        if response.status_code != 200:
            detail = response.text[:200]
            logger.warning(
                "Ollama returned status %s at %s: %s",
                response.status_code,
                url,
                detail,
            )
            return {
                "error": f"Ollama returned status {response.status_code}: {detail}",
                "model": model,
            }

        try:
            return cast(dict[str, Any], response.json())
        except ValueError:
            logger.warning("Ollama returned malformed JSON from %s", url)
            return {"error": "Ollama returned malformed JSON", "model": model}


def _validate_messages(messages: list[dict[str, str]]) -> str | None:
    """Return an error message if ``messages`` is malformed, else None."""
    if not messages:
        return "messages must be a non-empty list"
    for message in messages:
        if not isinstance(message, dict):
            return "each message must be a dict with 'role' and 'content'"
        if not isinstance(message.get("role"), str) or not isinstance(message.get("content"), str):
            return "each message must have string 'role' and 'content'"
    return None


def _str_or_default(config: dict[str, object], key: str, default: str) -> str:
    value = config.get(key, default)
    return str(value) if value is not None else default


def _timeout_or_default(config: dict[str, object]) -> float:
    """The configured ``llm.timeout_s`` in seconds, or the default.

    Accepts int or float; any other value (or none) keeps the default.
    """
    value = config.get("timeout_s")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_TIMEOUT_S
    return float(value)
