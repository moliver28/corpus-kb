"""TDD tests for OllamaEmbedder and FakeEmbedder."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from ollama._types import EmbedResponse

from src.rag.embedder import FakeEmbedder, OllamaEmbedder

_LIVE_CONFIG: dict[str, object] = {
    "embedding": {
        "provider": "ollama",
        "model": "qwen3-embedding:8b-q8_0",
        "base_url": "http://localhost:11434",
        "batch_size": 32,
        "dimensions": 4096,
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
    def test_embed_returns_vector_of_configured_dimensions(self) -> None:
        embedder = OllamaEmbedder(_LIVE_CONFIG)
        vector = embedder.embed("hello")

        assert isinstance(vector, list)
        assert len(vector) == embedder.dimensions
        assert all(isinstance(v, float) for v in vector)

    def test_embed_batch_returns_three_vectors_of_same_dimension(self) -> None:
        embedder = OllamaEmbedder(_LIVE_CONFIG)
        vectors = embedder.embed_batch(["alpha", "beta", "gamma"])

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
