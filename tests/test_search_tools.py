"""The shared search tool must wire the vector arm for non-server callers.

The server wiring builds the embedder once and hands it to the handler.
CLI, MCP, and any future caller go through ``tools.search_tools.search`` —
if that entry forgot the embedder, the vector arm silently vanished
(degraded reason ``no-embedder``) and every surface answered FTS-only.
"""

from __future__ import annotations

import asyncio

import pytest

from corpus_kb.tools import search_tools


class _SentinelEmbedder:
    pass


class _FakePool:
    async def close(self) -> None:  # pragma: no cover - interface parity
        return None


class _CapturingHandler:
    """Records the constructor args the tool passed to QueryHandler."""

    last_embedder: object = None
    last_config: object = None

    def __init__(self, pool: object, embedder: object = None, **kwargs: object) -> None:
        _CapturingHandler.last_embedder = embedder
        _CapturingHandler.last_config = kwargs.get("config")

    async def handle_search_with_meta(self, query: object) -> object:
        from corpus_kb.handlers.query_integrity import DegradedExcluded, SearchEnvelope

        return SearchEnvelope(
            status="success",
            results=[],
            degraded_excluded=DegradedExcluded(count=0, reasons=[]),
        )


@pytest.mark.asyncio
async def test_search_builds_embedder_from_config_when_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No embedder handed in → the config-driven embedder is built and used."""
    sentinel = _SentinelEmbedder()
    monkeypatch.setattr("corpus_kb.rag.create_embedder", lambda cfg, pool: sentinel)
    monkeypatch.setattr("corpus_kb.handlers.query_handler.QueryHandler", _CapturingHandler)

    await search_tools.search(_FakePool(), "anything", config={"embedding": {"provider": "ollama"}})

    assert _CapturingHandler.last_embedder is sentinel


@pytest.mark.asyncio
async def test_search_honors_explicit_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly wired embedder always wins (server-wiring parity)."""
    sentinel = _SentinelEmbedder()
    built: list[object] = []
    monkeypatch.setattr(
        "corpus_kb.rag.create_embedder",
        lambda cfg, pool: built.append(_SentinelEmbedder()) or _SentinelEmbedder(),
    )
    monkeypatch.setattr("corpus_kb.handlers.query_handler.QueryHandler", _CapturingHandler)

    await search_tools.search(
        _FakePool(), "anything", config={"embedding": {"provider": "ollama"}}, embedder=sentinel
    )

    assert _CapturingHandler.last_embedder is sentinel
    assert built == []


def test_search_is_async_callable() -> None:
    """Interface guard: the shared entry stays awaitable for every surface."""
    assert asyncio.iscoroutinefunction(search_tools.search)
