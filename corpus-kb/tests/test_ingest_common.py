"""Direct unit tests for ingest_common.py helpers."""

from __future__ import annotations

from typing import Any

import pytest

from src.rag.embedder import OllamaEmbedder
from src.tools.ingest_common import (
    _extract_entities_flag,
    _extractor_name,
    embed_chunks,
    load_config_or_pass,
    ontology,
)
from src.utils.models import Chunk


# ---------------------------------------------------------------------------
# load_config_or_pass
# ---------------------------------------------------------------------------


def test_load_config_or_pass_with_none_loads_config() -> None:
    """Passing None loads the default config."""
    config = load_config_or_pass(None)
    assert isinstance(config, dict)
    assert len(config) > 0


def test_load_config_or_pass_with_dict_returns_dict() -> None:
    """Passing a dict returns it unchanged."""
    custom: dict[str, object] = {"custom": True}
    result = load_config_or_pass(custom)
    assert result is custom


# ---------------------------------------------------------------------------
# ontology
# ---------------------------------------------------------------------------


def test_ontology_with_explicit_path() -> None:
    """Config with graph.ontology_path loads that ontology."""
    config: dict[str, object] = {"graph": {"ontology_path": "config/ontology.yaml"}}
    ont = ontology(config)
    assert len(ont.entity_types) == 9
    assert len(ont.relation_types) == 9


def test_ontology_with_fallback_path() -> None:
    """Config without graph.ontology_path falls back to default."""
    config: dict[str, object] = {"graph": {}}
    ont = ontology(config)
    assert len(ont.entity_types) == 9


# ---------------------------------------------------------------------------
# embed_chunks
# ---------------------------------------------------------------------------


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

    async def fetch(
        self, sql: str, model: str, texts: list[str]
    ) -> list[dict[str, list[float]]]:
        self.fetch_calls.append((sql, model, list(texts)))
        if self.fail:
            raise RuntimeError("pgml extension unavailable")
        value = 0.0 if self.zero else 0.5
        return [{"embed": [value] * self.dimensions} for _ in texts]


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


def _pgml_ingest_config(**overrides: object) -> dict[str, object]:
    """pgml-primary config whose ollama fallback points at a dead port."""
    embedding: dict[str, object] = {
        "provider": "pgml",
        "fallback_provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:65432",
        "batch_size": 100,
        "dimensions": 768,
    }
    embedding.update(overrides)
    return {"embedding": embedding}


async def test_embed_chunks_pgml_primary_success() -> None:
    """pgml primary produces real vectors: not degraded, fallback never needed."""
    conn = _FakePgmlConn()
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks, _pgml_ingest_config(), pool=_FakePool(conn)  # type: ignore[arg-type]
    )

    assert degraded is False
    assert error is None
    assert chunks[0].embedding == [0.5] * 768
    assert len(conn.fetch_calls) == 1


async def test_embed_chunks_pgml_zero_vectors_falls_back_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pgml zero-vectors: fallback ollama repopulates real vectors, degraded=True.

    The degraded flag must reflect that the primary failed (not misleading
    success), and the stale zero-vectors must not persist as embeddings.
    """
    conn = _FakePgmlConn(zero=True)
    real_vector = [0.7] * 768
    monkeypatch.setattr(
        OllamaEmbedder,
        "embed_batch",
        lambda self, texts: [list(real_vector) for _ in texts],
    )
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks, _pgml_ingest_config(), pool=_FakePool(conn)  # type: ignore[arg-type]
    )

    assert degraded is True
    assert error is not None
    assert "pgml" in error and "ollama" in error
    assert chunks[0].embedding == real_vector


async def test_embed_chunks_pgml_sql_error_falls_back_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pgml SQL failure (extension missing): fallback ollama vectors, degraded=True."""
    conn = _FakePgmlConn(fail=True)
    real_vector = [0.9] * 768
    monkeypatch.setattr(
        OllamaEmbedder,
        "embed_batch",
        lambda self, texts: [list(real_vector) for _ in texts],
    )
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks, _pgml_ingest_config(), pool=_FakePool(conn)  # type: ignore[arg-type]
    )

    assert degraded is True
    assert error is not None
    assert chunks[0].embedding == real_vector


async def test_embed_chunks_both_providers_fail_returns_zero_vectors() -> None:
    """pgml zeros + dead-port ollama fallback: degraded, zeros, message names both."""
    conn = _FakePgmlConn(zero=True)
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks, _pgml_ingest_config(), pool=_FakePool(conn)  # type: ignore[arg-type]
    )

    assert degraded is True
    assert error is not None
    assert "both returned zero vectors" in error
    assert "pgml" in error and "ollama" in error
    assert chunks[0].embedding == [0.0] * 768


async def test_embed_chunks_same_provider_skips_fallback_attempt() -> None:
    """provider == fallback_provider: fallback is not attempted a second time."""
    conn = _FakePgmlConn(zero=True)
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]

    degraded, error = await embed_chunks(
        chunks,
        _pgml_ingest_config(fallback_provider="pgml"),
        pool=_FakePool(conn),  # type: ignore[arg-type]
    )

    assert degraded is True
    assert error is not None
    assert "PgmlEmbedder returned zero vectors" in error
    assert len(conn.fetch_calls) == 1
    assert chunks[0].embedding == [0.0] * 768


async def test_embed_chunks_dead_port_returns_degraded_tuple() -> None:
    """embed_chunks with a dead port returns (True, error_string)."""
    config: dict[str, object] = {
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "dimensions": 768,
            "base_url": "http://localhost:99999",
            "batch_size": 32,
        }
    }
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]
    degraded, error = await embed_chunks(chunks, config)
    assert degraded is True
    assert error is not None
    assert isinstance(error, str)
    assert len(error) > 0


async def test_embed_chunks_success_returns_ok_tuple() -> None:
    """embed_chunks with a working Ollama returns (False, None)."""
    config: dict[str, object] = {
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "dimensions": 768,
            "base_url": "http://localhost:11434",
            "batch_size": 32,
        }
    }
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]
    degraded, error = await embed_chunks(chunks, config)
    # If Ollama is running, this should be (False, None).
    # If not running, it should be (True, error_string).
    assert isinstance(degraded, bool)
    if not degraded:
        assert error is None
    else:
        assert error is not None


# ---------------------------------------------------------------------------
# _extractor_name
# ---------------------------------------------------------------------------


def test_extractor_name_with_explicit_value() -> None:
    """_extractor_name returns the configured extractor."""
    config: dict[str, object] = {"graph": {"extractor": "langextract"}}
    assert _extractor_name(config) == "langextract"


def test_extractor_name_defaults_to_regex() -> None:
    """_extractor_name defaults to 'regex' when not configured."""
    config: dict[str, object] = {"graph": {}}
    assert _extractor_name(config) == "regex"


# ---------------------------------------------------------------------------
# _extract_entities_flag
# ---------------------------------------------------------------------------


def test_extract_entities_flag_true() -> None:
    """_extract_entities_flag returns True when explicitly set."""
    config: dict[str, object] = {"graph": {"extract_entities": True}}
    assert _extract_entities_flag(config) is True


def test_extract_entities_flag_false() -> None:
    """_extract_entities_flag returns False when explicitly disabled."""
    config: dict[str, object] = {"graph": {"extract_entities": False}}
    assert _extract_entities_flag(config) is False


def test_extract_entities_flag_defaults_true() -> None:
    """_extract_entities_flag defaults to True when not configured."""
    config: dict[str, object] = {"graph": {}}
    assert _extract_entities_flag(config) is True
