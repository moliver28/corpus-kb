"""TDD tests for OllamaEmbedder, PgmlEmbedder, FakeEmbedder, and create_embedder."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from ollama._types import EmbedResponse, ResponseError

from corpus_kb.errors import ConfigError
from corpus_kb.rag.embedder import (
    FakeEmbedder,
    OllamaEmbedder,
    PgmlEmbedder,
    create_embedder,
)

_LIVE_CONFIG: dict[str, object] = {
    "embedding": {
        "provider": "ollama",
        "model": "qwen3-embedding:4b",
        "base_url": "http://localhost:11434",
        "batch_size": 32,
        "dimensions": 2560,
    }
}

_FAKE_CONFIG: dict[str, object] = {
    "embedding": {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:11434",
        "batch_size": 32,
        "dimensions": 768,
    }
}

_DEAD_PORT_CONFIG: dict[str, object] = {
    "embedding": {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:65432",
        "batch_size": 32,
        "dimensions": 768,
    }
}


@pytest.mark.requires_ollama
class TestOllamaEmbedderLive:
    def _embed_batch_or_skip(self, embedder: OllamaEmbedder, texts: list[str]) -> list[list[float]]:
        try:
            return embedder.embed_batch(texts)
        except ResponseError as exc:
            if exc.status_code == 404:
                pytest.skip("Embedding model not available")
            raise

    def test_embed_returns_vector_of_configured_dimensions(self) -> None:
        embedder = OllamaEmbedder(_LIVE_CONFIG)
        vector = self._embed_batch_or_skip(embedder, ["hello"])[0]

        assert isinstance(vector, list)
        assert len(vector) == embedder.dimensions
        assert all(isinstance(v, float) for v in vector)

    def test_embed_batch_returns_three_vectors_of_same_dimension(self) -> None:
        embedder = OllamaEmbedder(_LIVE_CONFIG)
        vectors = self._embed_batch_or_skip(embedder, ["alpha", "beta", "gamma"])

        assert len(vectors) == 3
        for vector in vectors:
            assert len(vector) == embedder.dimensions


class TestOllamaEmbedderCache:
    def test_cache_hit_avoids_second_network_call(self) -> None:
        embedder = OllamaEmbedder(_FAKE_CONFIG)
        fake_response = EmbedResponse(embeddings=[[0.1] * embedder.dimensions])
        mock_embed = MagicMock(return_value=fake_response)
        embedder._client.embed = mock_embed

        vector_a = embedder.embed("hello")
        vector_b = embedder.embed("hello")

        assert vector_a == vector_b
        assert mock_embed.call_count == 1


class TestOllamaEmbedderDegradation:
    def test_dead_port_returns_zero_vector_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        embedder = OllamaEmbedder(_DEAD_PORT_CONFIG)

        with caplog.at_level(logging.WARNING):
            vector = embedder.embed("hello")

        assert len(vector) == embedder.dimensions
        assert all(v == 0.0 for v in vector)
        assert any("Ollama connection failed" in r.message for r in caplog.records)


class TestFakeEmbedder:
    def test_embed_returns_deterministic_vector_of_configured_dimensions(self) -> None:
        embedder = FakeEmbedder(_FAKE_CONFIG)
        vector_a = embedder.embed("hello")
        vector_b = embedder.embed("hello")

        assert len(vector_a) == embedder.dimensions
        assert vector_a == vector_b

    def test_different_texts_produce_different_vectors(self) -> None:
        embedder = FakeEmbedder(_FAKE_CONFIG)
        vector_a = embedder.embed("hello")
        vector_b = embedder.embed("world")

        assert vector_a != vector_b

    def test_embed_batch_returns_vectors_for_each_text(self) -> None:
        embedder = FakeEmbedder(_FAKE_CONFIG)
        vectors = embedder.embed_batch(["one", "two", "three"])

        assert len(vectors) == 3
        assert all(len(v) == embedder.dimensions for v in vectors)


# ---------------------------------------------------------------------------
# PgmlEmbedder — fake asyncpg pool/conn that records every SQL call
# ---------------------------------------------------------------------------


def _pgml_config(batch_size: int = 100) -> dict[str, object]:
    return {
        "embedding": {
            "provider": "pgml",
            "model": "nomic-embed-text",
            "batch_size": batch_size,
            "dimensions": 768,
        }
    }


class _FakeRecord:
    """Minimal stand-in for an asyncpg.Record row."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def __getitem__(self, key: object) -> list[float]:
        if key in ("embed", 0, "vector"):
            return self._vector
        raise KeyError(key)


class _FakePgmlConn:
    """Fake asyncpg connection answering pgml.embed(TEXT[]) calls."""

    def __init__(
        self,
        dimensions: int = 768,
        *,
        fail: bool = False,
        zero: bool = False,
    ) -> None:
        self.dimensions = dimensions
        self.fail = fail
        self.zero = zero
        self.fetch_calls: list[tuple[str, str, list[str]]] = []

    async def fetch(self, sql: str, model: str, texts: list[str]) -> list[_FakeRecord]:
        self.fetch_calls.append((sql, model, list(texts)))
        if self.fail:
            raise RuntimeError("pgml extension unavailable")
        value = 0.0 if self.zero else 0.5
        return [_FakeRecord([value] * self.dimensions) for _ in texts]


class _FakePool:
    """Fake asyncpg.Pool: acquire() returns an async context manager."""

    def __init__(self, conn: _FakePgmlConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _FakePgmlConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


class TestPgmlEmbedderBatch:
    async def test_embed_batch_one_sql_call_per_hundred_texts(self) -> None:
        """250 texts with batch_size=100 must produce exactly 3 TEXT[] SQL calls."""
        conn = _FakePgmlConn()
        embedder = PgmlEmbedder(_pgml_config(batch_size=100), pool=_FakePool(conn))  # type: ignore[arg-type]

        vectors = await embedder.embed_batch([f"text-{i}" for i in range(250)])

        assert len(vectors) == 250
        assert len(conn.fetch_calls) == 3
        assert [len(call[2]) for call in conn.fetch_calls] == [100, 100, 50]
        for sql, model, texts in conn.fetch_calls:
            assert "::text[]" in sql
            assert model == "nomic-embed-text"
            assert all(t.startswith("text-") for t in texts)

    async def test_embed_batch_empty_input_makes_no_sql_call(self) -> None:
        conn = _FakePgmlConn()
        embedder = PgmlEmbedder(_pgml_config(), pool=_FakePool(conn))  # type: ignore[arg-type]

        assert await embedder.embed_batch([]) == []
        assert conn.fetch_calls == []

    async def test_embed_batch_no_pool_returns_zero_vectors(self) -> None:
        embedder = PgmlEmbedder(_pgml_config(), pool=None)

        vectors = await embedder.embed_batch(["a", "b"])

        assert len(vectors) == 2
        assert all(v == [0.0] * 768 for v in vectors)

    async def test_embed_batch_sql_error_returns_zero_vectors(self) -> None:
        conn = _FakePgmlConn(fail=True)
        embedder = PgmlEmbedder(_pgml_config(), pool=_FakePool(conn))  # type: ignore[arg-type]

        vectors = await embedder.embed_batch(["a", "b", "c"])

        assert len(vectors) == 3
        assert all(v == [0.0] * 768 for v in vectors)

    async def test_embed_batch_size_over_100_is_clamped(self) -> None:
        """batch_size=500 must clamp to 100: 250 texts -> 3 calls, not 1."""
        conn = _FakePgmlConn()
        embedder = PgmlEmbedder(_pgml_config(batch_size=500), pool=_FakePool(conn))  # type: ignore[arg-type]

        await embedder.embed_batch([f"t{i}" for i in range(250)])

        assert len(conn.fetch_calls) == 3

    async def test_embed_batch_size_below_one_is_clamped(self) -> None:
        """batch_size=0 must clamp to 1: 3 texts -> 3 calls of 1 text each."""
        conn = _FakePgmlConn()
        embedder = PgmlEmbedder(_pgml_config(batch_size=0), pool=_FakePool(conn))  # type: ignore[arg-type]

        await embedder.embed_batch(["a", "b", "c"])

        assert len(conn.fetch_calls) == 3
        assert [len(call[2]) for call in conn.fetch_calls] == [1, 1, 1]

    async def test_embed_returns_first_vector(self) -> None:
        conn = _FakePgmlConn()
        embedder = PgmlEmbedder(_pgml_config(), pool=_FakePool(conn))  # type: ignore[arg-type]

        vector = await embedder.embed("hello")

        assert vector == [0.5] * 768
        assert len(conn.fetch_calls) == 1

    async def test_embed_batch_one_thousand_chunks_uses_at_most_ten_sql_calls(
        self,
    ) -> None:
        """1000 texts with batch_size=100 must use <=10 TEXT[] SQL round-trips."""
        conn = _FakePgmlConn()
        embedder = PgmlEmbedder(_pgml_config(batch_size=100), pool=_FakePool(conn))  # type: ignore[arg-type]

        texts = [f"chunk-{i}" for i in range(1000)]
        vectors = await embedder.embed_batch(texts)

        assert len(vectors) == 1000
        assert len(conn.fetch_calls) <= 10
        total_texts = sum(len(call[2]) for call in conn.fetch_calls)
        assert total_texts == 1000
        for sql, model, batch in conn.fetch_calls:
            assert "::text[]" in sql
            assert model == "nomic-embed-text"
            assert len(batch) <= 100


class TestCreateEmbedder:
    def test_pgml_provider_returns_pgml_embedder(self) -> None:
        embedder = create_embedder(_pgml_config(), pool=None)
        assert isinstance(embedder, PgmlEmbedder)

    def test_ollama_provider_returns_ollama_embedder(self) -> None:
        embedder = create_embedder(_FAKE_CONFIG, pool=None)
        assert isinstance(embedder, OllamaEmbedder)

    def test_missing_provider_defaults_to_ollama(self) -> None:
        # The shipped provider default flipped pgml -> ollama (a 4B embedder
        # cannot be served in-database).
        config: dict[str, object] = {"embedding": {"dimensions": 768}}
        embedder = create_embedder(config, pool=None)
        assert isinstance(embedder, OllamaEmbedder)

    def test_unknown_provider_raises_clear_error(self) -> None:
        config: dict[str, object] = {"embedding": {"provider": "cloud-api"}}
        with pytest.raises(ValueError, match="cloud-api"):
            create_embedder(config, pool=None)


# ---------------------------------------------------------------------------
# Dimensions passthrough + returned-width validation (config v2)
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Stand-in for ollama.Client recording the embed() kwargs."""

    def __init__(self, embeddings: list[list[float]]) -> None:
        self._embeddings = embeddings
        self.calls: list[dict[str, object]] = []

    def embed(self, **kwargs: object) -> EmbedResponse:
        self.calls.append(dict(kwargs))
        return EmbedResponse(embeddings=self._embeddings)


class TestDimensionsPassthrough:
    def _embedder(self, config: dict[str, object], client: _RecordingClient) -> OllamaEmbedder:
        embedder = OllamaEmbedder(config)
        embedder._client = client
        return embedder

    def test_mrl_and_lower_dims_pass_dimensions(self) -> None:
        client = _RecordingClient([[0.1] * 1024])
        embedder = self._embedder(
            {
                "embedding": {
                    "model": "qwen3-embedding:4b",
                    "mrl_trained": True,
                    "dimensions": 1024,
                }
            },
            client,
        )
        assert embedder.embed("q") == [0.1] * 1024
        assert client.calls[0]["dimensions"] == 1024

    def test_non_mrl_omits_dimensions(self) -> None:
        client = _RecordingClient([[0.1] * 768])
        embedder = self._embedder(
            {
                "embedding": {
                    "model": "nomic-embed-text",
                    "mrl_trained": False,
                    "dimensions": 768,
                }
            },
            client,
        )
        embedder.embed("q")
        assert "dimensions" not in client.calls[0]

    def test_mrl_at_native_width_omits_dimensions(self) -> None:
        # Requesting the native width must stay byte-identical to the
        # pre-v2 call (no dimensions kwarg at all).
        client = _RecordingClient([[0.1] * 2560])
        embedder = self._embedder(
            {
                "embedding": {
                    "model": "qwen3-embedding:4b",
                    "mrl_trained": True,
                    "dimensions": 2560,
                }
            },
            client,
        )
        embedder.embed("q")
        assert "dimensions" not in client.calls[0]

    def test_mrl_unknown_model_omits_dimensions(self) -> None:
        client = _RecordingClient([[0.1] * 512])
        embedder = self._embedder(
            {
                "embedding": {
                    "model": "some-unknown-model",
                    "mrl_trained": True,
                    "dimensions": 512,
                }
            },
            client,
        )
        embedder.embed("q")
        assert "dimensions" not in client.calls[0]


class TestReturnedWidthValidation:
    def test_wrong_width_raises_config_error_with_remediation(self) -> None:
        # The model emitted 768 dims but embedding.dimensions says 2560.
        client = _RecordingClient([[0.1] * 768])
        embedder = OllamaEmbedder(
            {
                "embedding": {
                    "model": "nomic-embed-text",
                    "dimensions": 2560,
                }
            }
        )
        embedder._client = client
        with pytest.raises(ConfigError) as excinfo:
            embedder.embed("q")
        assert "768" in str(excinfo.value)
        assert "2560" in str(excinfo.value)
        assert "native" in excinfo.value.remediation

    def test_matching_width_passes(self) -> None:
        client = _RecordingClient([[0.1] * 768])
        embedder = OllamaEmbedder(
            {
                "embedding": {
                    "model": "nomic-embed-text",
                    "dimensions": 768,
                }
            }
        )
        embedder._client = client
        assert embedder.embed("q") == [0.1] * 768


# ---------------------------------------------------------------------------
# Matryoshka slice/normalize helpers (Feature 4)
# ---------------------------------------------------------------------------


def test_slice_normalize_produces_unit_vector() -> None:
    from corpus_kb.rag.embedder import _slice_normalize

    v = _slice_normalize([3.0, 4.0, 0.0, 0.0], dim=2)
    assert len(v) == 2
    norm = sum(x * x for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_slice_normalize_zero_vector_is_safe() -> None:
    from corpus_kb.rag.embedder import _slice_normalize

    v = _slice_normalize([0.0, 0.0, 0.0, 0.0], dim=2)
    assert v == [0.0, 0.0]


def test_fake_embedder_matryoshka_matches_dim() -> None:
    e = FakeEmbedder({"embedding": {"dimensions": 4096}})
    v = e.embed_matryoshka("x", 1024)
    assert len(v) == 1024
