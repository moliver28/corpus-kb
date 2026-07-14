"""Degraded-mode pipeline test (Ollama unavailable)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from src.config import load_config
from src.tools.ingest_tools import ingest_file

_FIXTURE_DIR = Path(__file__).with_name("fixtures") / "langextract_recorded"
_SAMPLE_MD = Path(__file__).with_name("fixtures") / "ontology_sample.md"


def _build_degraded_config() -> dict[str, object]:
    """Build a config that points Ollama at a dead port."""
    config = load_config()
    graph = cast(dict[str, object], config.setdefault("graph", {}))
    graph["extractor"] = "langextract"
    graph["fixture_dir"] = str(_FIXTURE_DIR.resolve())
    graph["live_fallback"] = False
    embedding = cast(dict[str, object], config.setdefault("embedding", {}))
    embedding["model"] = "nomic-embed-text"
    embedding["dimensions"] = 768
    embedding["base_url"] = "http://localhost:99999"
    embedding["batch_size"] = 32
    return config


@pytest.mark.asyncio
async def test_degraded_mode_ollama_unavailable(pg_pool) -> None:
    """Ingest with a dead Ollama port — pipeline succeeds in degraded mode."""
    config = _build_degraded_config()
    result = await ingest_file(str(_SAMPLE_MD), pg_pool, config=config)

    assert result["status"] == "success"
    assert result["degraded"] is True
    assert isinstance(result["errors"], list)
    assert len(result["errors"]) >= 1
    entity_count = result["entity_count"]
    assert isinstance(entity_count, int)
    pg_chunk_count = result.get("pg_chunk_count", 0)
    assert isinstance(pg_chunk_count, int)