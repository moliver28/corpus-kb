"""Degraded-mode pipeline test (Ollama unavailable)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from src.config import load_config
from src.storage.graph_store import SQLiteGraphStore
from src.tools.ingest_tools import ingest_file

_FIXTURE_DIR = Path(__file__).with_name("fixtures") / "langextract_recorded"
_SAMPLE_MD = Path(__file__).with_name("fixtures") / "ontology_sample.md"


def _build_degraded_config(tmp_path: Path) -> dict[str, object]:
    """Build a config that points Ollama at a dead port."""
    config = load_config()
    storage = cast(dict[str, object], config.setdefault("storage", {}))
    storage["lancedb_uri"] = str(tmp_path / "lancedb")
    storage["graph_db"] = str(tmp_path / "graph.db")
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


def test_degraded_mode_ollama_unavailable(tmp_path: Path) -> None:
    """Ingest with a dead Ollama port — pipeline succeeds in degraded mode.

    Verifies:
    - status is "success" (pipeline does not crash)
    - degraded is True (embeddings failed)
    - errors is a non-empty list (structured error reporting)
    - entity_count >= 1 (extraction still works without embeddings)
    - lance_row_count >= 1 (zero-vector fallback still stores vectors)
    """
    config = _build_degraded_config(tmp_path)
    store = SQLiteGraphStore(tmp_path / "graph.db")

    result = ingest_file(str(_SAMPLE_MD), graph_store=store, config=config)

    assert result["status"] == "success"
    assert result["degraded"] is True
    assert isinstance(result["errors"], list)
    assert len(result["errors"]) >= 1
    entity_count = result["entity_count"]
    assert isinstance(entity_count, int)
    assert entity_count >= 1
    lance_count = result["lance_row_count"]
    assert isinstance(lance_count, int)
    assert lance_count >= 1
