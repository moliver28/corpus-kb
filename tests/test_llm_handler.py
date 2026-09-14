"""TDD tests for LlmHandler — direct Ollama HTTP access for chat/generate."""

from __future__ import annotations

import logging
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from corpus_kb.config import get_default_config
from corpus_kb.handlers import LlmHandler
from corpus_kb.handlers.llm_handler import LlmHandler as DirectLlmHandler

_CONFIG: dict[str, object] = {
    "llm": {
        "model": "qwen3:4b",
        "base_url": "http://localhost:11434",
    }
}

_DEAD_PORT_CONFIG: dict[str, object] = {
    "llm": {
        "model": "qwen3:4b",
        "base_url": "http://localhost:65432",
    }
}

_MESSAGES = [{"role": "user", "content": "Hello"}]

_CHAT_PAYLOAD = {
    "message": {"role": "assistant", "content": "Hi! How can I help?"},
    "done": True,
}

_GENERATE_PAYLOAD = {
    "response": "Hi! How can I help?",
    "done": True,
}


class TestLlmHandlerConfig:
    def test_handler_defaults_when_llm_section_missing(self) -> None:
        handler = LlmHandler({})

        assert handler.model == "qwen3:4b"
        assert handler.base_url == "http://localhost:11434"

    def test_default_config_contains_llm_section(self) -> None:
        llm = cast(dict[str, object], get_default_config()["llm"])

        assert llm["model"] == "qwen3:4b"
        assert llm["base_url"] == "http://localhost:11434"

    def test_custom_config_is_respected(self) -> None:
        handler = LlmHandler(
            {"llm": {"model": "qwen3:0.6b", "base_url": "http://host:1234"}}
        )

        assert handler.model == "qwen3:0.6b"
        assert handler.base_url == "http://host:1234"

    def test_exported_from_handlers_package(self) -> None:
        assert LlmHandler is DirectLlmHandler


class TestLlmHandlerChat:
    async def test_chat_returns_parsed_response_on_success(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock(return_value=httpx.Response(200, json=_CHAT_PAYLOAD))

        with patch.object(httpx.AsyncClient, "post", post):
            result = await handler.chat(_MESSAGES)

        assert result == _CHAT_PAYLOAD
        assert result["message"]["content"] == "Hi! How can I help?"
        post.assert_awaited_once_with(
            "http://localhost:11434/api/chat",
            json={"model": "qwen3:4b", "messages": _MESSAGES, "stream": False},
        )

    async def test_chat_uses_model_override(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock(return_value=httpx.Response(200, json=_CHAT_PAYLOAD))

        with patch.object(httpx.AsyncClient, "post", post):
            await handler.chat(_MESSAGES, model="qwen3:0.6b")

        assert post.await_args.kwargs["json"]["model"] == "qwen3:0.6b"

    async def test_chat_non_200_returns_error_dict(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock(
            return_value=httpx.Response(500, json={"error": "model not found"})
        )

        with patch.object(httpx.AsyncClient, "post", post):
            result = await handler.chat(_MESSAGES)

        assert "error" in result
        assert "500" in result["error"]
        assert "message" not in result

    async def test_chat_malformed_json_returns_error_dict(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock(return_value=httpx.Response(200, content=b"<html>"))

        with patch.object(httpx.AsyncClient, "post", post):
            result = await handler.chat(_MESSAGES)

        assert "error" in result
        assert "message" not in result


class TestLlmHandlerGenerate:
    async def test_generate_returns_parsed_response_on_success(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock(return_value=httpx.Response(200, json=_GENERATE_PAYLOAD))

        with patch.object(httpx.AsyncClient, "post", post):
            result = await handler.generate("Say hi")

        assert result == _GENERATE_PAYLOAD
        assert result["response"] == "Hi! How can I help?"
        post.assert_awaited_once_with(
            "http://localhost:11434/api/generate",
            json={"model": "qwen3:4b", "prompt": "Say hi", "stream": False},
        )

    async def test_generate_uses_model_override(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock(return_value=httpx.Response(200, json=_GENERATE_PAYLOAD))

        with patch.object(httpx.AsyncClient, "post", post):
            await handler.generate("Say hi", model="qwen3:0.6b")

        assert post.await_args.kwargs["json"]["model"] == "qwen3:0.6b"

    async def test_generate_non_200_returns_error_dict(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock(return_value=httpx.Response(404, json={"error": "no model"}))

        with patch.object(httpx.AsyncClient, "post", post):
            result = await handler.generate("Say hi")

        assert "error" in result
        assert "404" in result["error"]
        assert "response" not in result


class TestLlmHandlerValidation:
    async def test_chat_empty_messages_returns_error_without_http(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock()

        with patch.object(httpx.AsyncClient, "post", post):
            result = await handler.chat([])

        assert "error" in result
        post.assert_not_called()

    async def test_chat_malformed_message_returns_error_without_http(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock()

        with patch.object(httpx.AsyncClient, "post", post):
            result = await handler.chat([{"role": "user"}])

        assert "error" in result
        post.assert_not_called()

    async def test_generate_empty_prompt_returns_error_without_http(self) -> None:
        handler = LlmHandler(_CONFIG)
        post = AsyncMock()

        with patch.object(httpx.AsyncClient, "post", post):
            result = await handler.generate("")

        assert "error" in result
        post.assert_not_called()


class TestLlmHandlerDegradation:
    async def test_chat_dead_port_returns_error_dict_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        handler = LlmHandler(_DEAD_PORT_CONFIG, timeout=2.0)

        with caplog.at_level(logging.WARNING):
            result = await handler.chat(_MESSAGES)

        assert "error" in result
        assert "message" not in result
        assert any("Ollama" in r.message for r in caplog.records)

    async def test_generate_dead_port_returns_error_dict_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        handler = LlmHandler(_DEAD_PORT_CONFIG, timeout=2.0)

        with caplog.at_level(logging.WARNING):
            result = await handler.generate("Say hi")

        assert "error" in result
        assert "response" not in result
        assert any("Ollama" in r.message for r in caplog.records)
