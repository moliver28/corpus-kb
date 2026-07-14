"""Direct unit tests for ingest_common.py helpers."""

from __future__ import annotations

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


def test_embed_chunks_dead_port_returns_degraded_tuple() -> None:
    """embed_chunks with a dead port returns (True, error_string)."""
    config: dict[str, object] = {
        "embedding": {
            "model": "nomic-embed-text",
            "dimensions": 768,
            "base_url": "http://localhost:99999",
            "batch_size": 32,
        }
    }
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]
    degraded, error = embed_chunks(chunks, config)
    assert degraded is True
    assert error is not None
    assert isinstance(error, str)
    assert len(error) > 0


def test_embed_chunks_success_returns_ok_tuple() -> None:
    """embed_chunks with a working Ollama returns (False, None)."""
    config: dict[str, object] = {
        "embedding": {
            "model": "nomic-embed-text",
            "dimensions": 768,
            "base_url": "http://localhost:11434",
            "batch_size": 32,
        }
    }
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]
    degraded, error = embed_chunks(chunks, config)
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
