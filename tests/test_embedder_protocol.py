"""Tests for the Embedder Protocol and query-side instruct() prefix."""

from __future__ import annotations

from corpus_kb.rag.embedder import (
    QUERY_INSTRUCTION_PREFIX,
    Embedder,
    FakeEmbedder,
    OllamaEmbedder,
    instruct,
)

_FAKE_CONFIG: dict[str, object] = {
    "embedding": {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:11434",
        "batch_size": 32,
        "dimensions": 768,
    }
}


class TestInstruct:
    def test_instruct_returns_exact_prefixed_text(self) -> None:
        expected = (
            "Instruct: Given a qualitative research query, "
            "retrieve relevant interview exchanges\nQuery: "
            "some query"
        )
        assert instruct("some query") == expected

    def test_instruct_prepends_the_module_constant(self) -> None:
        assert instruct("text") == QUERY_INSTRUCTION_PREFIX + "text"

    def test_prefix_constant_matches_contract(self) -> None:
        assert QUERY_INSTRUCTION_PREFIX == (
            "Instruct: Given a qualitative research query, "
            "retrieve relevant interview exchanges\nQuery: "
        )

    def test_empty_text_yields_bare_prefix(self) -> None:
        assert instruct("") == QUERY_INSTRUCTION_PREFIX


class TestEmbedderProtocol:
    def test_fake_embedder_satisfies_protocol(self) -> None:
        assert isinstance(FakeEmbedder(_FAKE_CONFIG), Embedder)

    def test_ollama_embedder_satisfies_protocol(self) -> None:
        assert isinstance(OllamaEmbedder(_FAKE_CONFIG), Embedder)

    def test_structural_class_without_inheritance_satisfies_protocol(self) -> None:
        class _Minimal:
            dimensions = 3

            def embed(self, text: str) -> list[float]:
                return [0.0]

            def embed_batch(self, texts: list[str]) -> list[list[float]]:
                return [[0.0] for _ in texts]

            def instruct(self, text: str) -> str:
                return QUERY_INSTRUCTION_PREFIX + text

        assert isinstance(_Minimal(), Embedder)

    def test_protocol_instruct_matches_module_function(self) -> None:
        embedder: Embedder = FakeEmbedder(_FAKE_CONFIG)
        assert embedder.instruct("q") == instruct("q")

    def test_protocol_surfaces_dimensions(self) -> None:
        embedder: Embedder = FakeEmbedder(_FAKE_CONFIG)
        assert embedder.dimensions == 768
