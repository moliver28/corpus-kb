"""Tests for the persistent embedding cache (issue #539).

Covers the two-tier cache in OllamaEmbedder: a DB hit must skip the HTTP
call, a model flip must never serve a stale vector, degraded zero vectors
must never be stored, and both gateway backends must work. The DuckDB leg
runs the real migration set twice against a temp file, which also proves
migration 046 is idempotent.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from ollama._types import EmbedResponse

from corpus_kb.rag.embedder import OllamaEmbedder, _sha256_key
from corpus_kb.rag.embedder_cache import SCHEMA_EMBEDDING_WIDTH, EmbeddingCache
from corpus_kb.storage.gateway import DuckDbGateway

_DIM = SCHEMA_EMBEDDING_WIDTH

_CONFIG: dict[str, object] = {
    "embedding": {
        "provider": "ollama",
        "model": "qwen3-embedding:4b",
        "base_url": "http://localhost:11434",
        "batch_size": 32,
        "dimensions": _DIM,
    }
}


class FakePgGateway:
    """In-memory Postgres-shaped gateway: stores halfvec text literals."""

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, str]]:
        keys, model = args
        return [
            {"sha256": key, "vector": vector}
            for key, (vector, row_model) in self.rows.items()
            if key in keys and row_model == model
        ]

    async def executemany(self, query: str, args: Any) -> None:
        for key, model, vector in args:
            self.rows.setdefault(key, (vector, model))


def _mock_client(embedder: OllamaEmbedder) -> MagicMock:
    mock = MagicMock()

    def _respond(**kwargs: object) -> EmbedResponse:
        inputs = list(kwargs["input"])
        return EmbedResponse(
            embeddings=[[0.25] * embedder.dimensions for _ in inputs], prompt_eval_count=7
        )

    mock.embed = MagicMock(side_effect=_respond)
    embedder._client.embed = mock.embed
    return mock


def _make_embedder(cache: EmbeddingCache | None) -> OllamaEmbedder:
    embedder = OllamaEmbedder(dict(_CONFIG), cache=cache)
    return embedder


def test_sha256_key_covers_instruction_prefix() -> None:
    """Instruction invalidation rides the key: instructed text hashes apart."""
    assert _sha256_key("query") != _sha256_key("Instruct: x\nQuery: query")


def test_db_hit_skips_http() -> None:
    """A row already in the table answers the batch with no model call."""
    gateway = FakePgGateway()
    key = _sha256_key("hello world")
    gateway.rows[key] = ("[" + ",".join(["0.5"] * _DIM) + "]", "qwen3-embedding:4b")
    cache = EmbeddingCache(gateway, "qwen3-embedding:4b", _DIM)
    embedder = _make_embedder(cache)
    mock = _mock_client(embedder)

    vectors = embedder.embed_batch(["hello world"])

    mock.embed.assert_not_called()
    assert vectors[0] == [0.5] * _DIM
    assert embedder.last_usage is not None
    assert embedder.last_usage.cached_texts == 1
    assert embedder.last_usage.embedded_texts == 0
    assert embedder.last_usage.prompt_tokens is None


def test_db_hit_mixed_batch_calls_http_once_for_misses() -> None:
    """Misses still embed; hits count as cached in the usage snapshot."""
    gateway = FakePgGateway()
    hit = _sha256_key("cached text")
    gateway.rows[hit] = ("[" + ",".join(["0.5"] * _DIM) + "]", "qwen3-embedding:4b")
    cache = EmbeddingCache(gateway, "qwen3-embedding:4b", _DIM)
    embedder = _make_embedder(cache)
    mock = _mock_client(embedder)

    vectors = embedder.embed_batch(["cached text", "fresh text"])

    mock.embed.assert_called_once()
    assert vectors[0] == [0.5] * _DIM
    assert len(vectors[1]) == _DIM
    assert embedder.last_usage is not None
    assert embedder.last_usage.cached_texts == 1
    assert embedder.last_usage.embedded_texts == 1


def test_model_mismatch_is_a_miss() -> None:
    """A row stored under another model must never be served."""
    gateway = FakePgGateway()
    gateway.rows[_sha256_key("hello world")] = (
        "[" + ",".join(["0.5"] * _DIM) + "]",
        "nomic-embed-text",
    )
    cache = EmbeddingCache(gateway, "qwen3-embedding:4b", _DIM)
    embedder = _make_embedder(cache)
    mock = _mock_client(embedder)

    vectors = embedder.embed_batch(["hello world"])

    mock.embed.assert_called_once()
    assert vectors[0] == [0.25] * _DIM


def test_fresh_vectors_are_stored_then_served_without_http() -> None:
    """First call embeds and stores; a second embedder on the same table hits."""
    gateway = FakePgGateway()
    cache = EmbeddingCache(gateway, "qwen3-embedding:4b", _DIM)
    first = _make_embedder(cache)
    _mock_client(first)
    first.embed_batch(["persist me"])

    assert _sha256_key("persist me") in gateway.rows

    second_cache = EmbeddingCache(gateway, "qwen3-embedding:4b", _DIM)
    second = _make_embedder(second_cache)
    mock = _mock_client(second)
    vectors = second.embed_batch(["persist me"])
    mock.embed.assert_not_called()
    assert vectors[0] == [0.25] * _DIM


def test_degraded_zero_vectors_are_not_stored() -> None:
    """The fallback's zero vectors must never become table rows."""
    gateway = FakePgGateway()
    cache = EmbeddingCache(gateway, "qwen3-embedding:4b", _DIM)
    embedder = _make_embedder(cache)

    embedder._client.embed = MagicMock(side_effect=ConnectionError("down"))
    embedder.embed_batch(["will degrade"])

    assert gateway.rows == {}
    assert embedder.last_note is not None


def test_width_mismatch_skips_cache() -> None:
    """An embedder at a non-schema width runs RAM-only."""
    gateway = FakePgGateway()
    config = dict(_CONFIG)
    config["embedding"] = {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:11434",
        "batch_size": 32,
        "dimensions": 768,
    }
    cache = EmbeddingCache(gateway, "nomic-embed-text", 768)
    assert cache.enabled is False
    embedder = OllamaEmbedder(dict(config), cache=cache)
    _mock_client(embedder)
    embedder.embed_batch(["narrow"])
    assert gateway.rows == {}


@pytest.mark.asyncio
async def test_duckdb_backend_roundtrip(tmp_path: Any) -> None:
    """The real DuckDB migration set twice, then a store-lookup roundtrip.

    Running the migrations twice proves migration 046 is idempotent. See
    the old-fixture-DB gotcha: stale fixture databases that skipped
    migrations 004/005 need apply_schema after a new migration lands.
    """
    from corpus_kb._setup.migrate import run_duckdb_migrations

    dsn = f"duckdb:///{(tmp_path / 'cache.duckdb').as_posix()}"
    run_duckdb_migrations(dsn)
    run_duckdb_migrations(dsn)
    gateway = await DuckDbGateway.connect(dsn)
    try:
        cache = EmbeddingCache(gateway, "qwen3-embedding:4b", _DIM)
        vector = [0.1 * (i + 1) for i in range(_DIM)]
        await cache.store([_sha256_key("duck text")], [vector])
        found = await cache.lookup([_sha256_key("duck text")])
        assert list(found.keys()) == [_sha256_key("duck text")]
        assert found[_sha256_key("duck text")] == pytest.approx(vector)

        # A row under another model must not come back.
        missed = await cache.lookup([_sha256_key("duck text")])  # same model hit
        assert missed
        other = EmbeddingCache(gateway, "another-model", _DIM)
        assert await other.lookup([_sha256_key("duck text")]) == {}
    finally:
        await gateway.close()


def test_duckdb_store_sync_serves_second_process(tmp_path: Any) -> None:
    """The sync path used by embed_batch works against a real DuckDB file."""
    from corpus_kb._setup.migrate import run_duckdb_migrations

    dsn = f"duckdb:///{(tmp_path / 'sync.duckdb').as_posix()}"
    run_duckdb_migrations(dsn)
    gateway = asyncio.run(DuckDbGateway.connect(dsn))
    cache = EmbeddingCache(gateway, "qwen3-embedding:4b", _DIM)
    embedder = _make_embedder(cache)
    mock = _mock_client(embedder)

    embedder.embed_batch(["cold process text"])
    assert mock.embed.call_count == 1

    second = _make_embedder(EmbeddingCache(gateway, "qwen3-embedding:4b", _DIM))
    mock2 = _mock_client(second)
    vectors = second.embed_batch(["cold process text"])
    mock2.embed.assert_not_called()
    assert vectors[0] == [0.25] * _DIM
    asyncio.run(gateway.close())


def test_create_embedder_attaches_cache() -> None:
    """A pool or gateway in, a persistent cache on the embedder out."""
    from corpus_kb.rag.embedder_factory import create_embedder

    gateway = FakePgGateway()
    embedder = create_embedder(dict(_CONFIG), gateway)
    assert embedder._persist_cache is not None
    assert embedder._persist_cache.enabled is True

    bare = create_embedder(dict(_CONFIG), None)
    assert bare._persist_cache is None
