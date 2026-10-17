"""The ask tool wiring: embedder, handler, judge, envelope (offline).

Mirrors ``tests/test_search_tools.py``: if ``tools.ask_tools.ask`` forgot
the embedder the vector arm would silently vanish, and if it skipped the
judge the verifier would abstain on every call. Both seams are captured
here without a database or Ollama.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar
from uuid import uuid4

import pytest

from corpus_kb.tools import ask_tools


class _SentinelEmbedder:
    pass


class _SentinelJudge:
    def __init__(self, config: object) -> None:
        self.config = config


class _FakePool:
    async def close(self) -> None:  # pragma: no cover - interface parity
        return None


class _CapturingHandler:
    """Canned search envelope; records the constructor args the tool passed."""

    last_pool: object = None
    last_embedder: object = None
    last_config: object = None

    def __init__(self, pool: object, embedder: object = None, **kwargs: object) -> None:
        _CapturingHandler.last_pool = pool
        _CapturingHandler.last_embedder = embedder
        _CapturingHandler.last_config = kwargs.get("config")

    async def handle_search_with_meta(self, query: object) -> object:
        from corpus_kb.domain.models import SearchResult
        from corpus_kb.handlers.query_integrity import DegradedExcluded, SearchEnvelope

        result = SearchResult(
            chunk_id=uuid4(),
            text="stored fact",
            score=0.9,
            source="a.txt",
            doc_id=uuid4(),
        )
        return SearchEnvelope(
            status="success",
            results=[result],
            degraded_excluded=DegradedExcluded(count=0, reasons=[]),
        )


class _CannedLlm:
    model = "fake-model"

    async def chat(
        self, messages: list[dict[str, str]], model: str | None = None, options: object = None
    ) -> dict[str, object]:
        return {"message": {"content": "Stored fact [1]."}}


class _RecordingLlm(_CannedLlm):
    built: ClassVar[list[object]] = []

    def __init__(self, config: object) -> None:
        _RecordingLlm.built.append(config)


def _patch_world(monkeypatch: pytest.MonkeyPatch, cfg: dict[str, Any]) -> list[object]:
    """Stub the embedder, handler, and LLM seams the tool resolves per call."""
    built: list[object] = []

    def _fake_create(embedder_cfg: object, pool: object) -> _SentinelEmbedder:
        built.append(pool)
        return _SentinelEmbedder()

    monkeypatch.setattr("corpus_kb.rag.create_embedder", _fake_create)
    monkeypatch.setattr("corpus_kb.handlers.query_handler.QueryHandler", _CapturingHandler)
    monkeypatch.setattr("corpus_kb.handlers.llm_handler.LlmHandler", _RecordingLlm)
    return built


async def test_ask_builds_embedder_and_handler_over_the_callers_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pools = _patch_world(monkeypatch, {})
    pool = _FakePool()

    envelope = await ask_tools.ask("why does it fail?", config={"embedding": {}}, pg_pool=pool)

    assert envelope["status"] == "success"
    assert set(envelope) == {"status", "result", "degraded_excluded"}
    assert _CapturingHandler.last_pool is pool
    assert isinstance(_CapturingHandler.last_embedder, _SentinelEmbedder)
    assert _CapturingHandler.last_config == {"embedding": {}}
    assert pools == [pool]
    assert _RecordingLlm.built == [{"embedding": {}}]
    result = envelope["result"]
    assert result["answer"] == "Stored fact [1]."
    assert [s["citations"] for s in result["sentences"]] == [[1]]


async def test_ask_wires_the_judge_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """judge.enabled passes the judge to compose; disabled passes None."""
    seen: dict[str, object] = {}

    async def _spy(question: str, handler: object, llm: object, **kwargs: object) -> object:
        seen["judge"] = kwargs.get("judge")
        from corpus_kb.ask.composer import STATUS_EMPTY, AnswerBundle

        return AnswerBundle(STATUS_EMPTY, "", [], [], {"abstained": True}, "m", None, {})

    _patch_world(monkeypatch, {})
    monkeypatch.setattr("corpus_kb.ask.composer.compose_answer", _spy)

    await ask_tools.ask("q", config={}, pg_pool=_FakePool())
    assert seen["judge"] is None

    monkeypatch.setattr("corpus_kb.rag.judge.OllamaJudge", _SentinelJudge)
    await ask_tools.ask("q", config={"judge": {"enabled": True}}, pg_pool=_FakePool())
    assert isinstance(seen["judge"], _SentinelJudge)


async def test_ask_is_async_callable() -> None:
    """Interface guard: the shared entry stays awaitable for every surface."""
    assert asyncio.iscoroutinefunction(ask_tools.ask)
