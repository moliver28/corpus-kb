"""The shared search tool must wire the vector arm for non-server callers.

The server wiring builds the embedder once and hands it to the handler.
CLI, MCP, and any future caller go through ``tools.search_tools.search`` —
if that entry forgot the embedder, the vector arm silently vanished
(degraded reason ``no-embedder``) and every surface answered FTS-only.
The tool therefore builds the config-driven embedder per call; the
signature stays unchanged so the MCP tool contract is untouched.
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
async def test_search_builds_embedder_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The config-driven embedder is built per call and handed to the handler."""
    built: list[object] = []

    def _fake_create(cfg: object, pool: object) -> _SentinelEmbedder:
        built.append((cfg, pool))
        return _SentinelEmbedder()

    monkeypatch.setattr("corpus_kb.rag.create_embedder", _fake_create)
    monkeypatch.setattr("corpus_kb.handlers.query_handler.QueryHandler", _CapturingHandler)
    cfg = {"embedding": {"provider": "ollama"}}

    await search_tools.search(_FakePool(), "anything", config=cfg)

    assert isinstance(_CapturingHandler.last_embedder, _SentinelEmbedder)
    assert built and built[0][0] is cfg
    assert _CapturingHandler.last_config is cfg


def test_search_signature_is_stable() -> None:
    """The MCP tool contract snapshots this signature: it must not grow."""
    import inspect

    params = list(inspect.signature(search_tools.search).parameters)
    assert params == [
        "pg_pool",
        "query",
        "config",
        "tenant_id",
        "k",
        "source_type",
        "self_query",
    ]


def test_search_is_async_callable() -> None:
    """Interface guard: the shared entry stays awaitable for every surface."""
    assert asyncio.iscoroutinefunction(search_tools.search)
