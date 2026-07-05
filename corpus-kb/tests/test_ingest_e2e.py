"""End-to-end ontology ingest tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from src.config import load_config
from src.ontology import load_ontology
from src.storage.graph_store import SQLiteGraphStore
from src.tools.ingest_tools import ingest_file
from src.utils.models import Document, Entity

_FIXTURE_DIR = Path(__file__).with_name("fixtures") / "langextract_recorded"
_SAMPLE_MD = Path(__file__).with_name("fixtures") / "ontology_sample.md"
_ONTOLOGY_PATH = Path("config/ontology.yaml")


def _build_config(tmp_path: Path) -> dict[str, object]:
    config = load_config()
    storage = cast(dict[str, object], config.setdefault("storage", {}))
    storage["lancedb_uri"] = str(tmp_path / "lancedb")
    storage["graph_db"] = str(tmp_path / "graph.db")
    graph = cast(dict[str, object], config.setdefault("graph", {}))
    graph["extractor"] = "langextract"
    graph["fixture_dir"] = str(_FIXTURE_DIR.resolve())
    graph["live_fallback"] = False
    embedding = cast(dict[str, object], config.setdefault("embedding", {}))
    embedding.setdefault("model", "nomic-embed-text")
    embedding.setdefault("dimensions", 768)
    embedding.setdefault("base_url", "http://localhost:11434")
    embedding.setdefault("batch_size", 32)
    return config


def _chunk_map(store: SQLiteGraphStore) -> dict[str, str]:
    with store._open_connection() as conn:
        return {
            row[0]: row[1]
            for row in conn.execute("SELECT chunk_id, text FROM chunks").fetchall()
        }


def _load_entities(store: SQLiteGraphStore, name_to_id: dict[str, str]) -> list[Entity]:
    entities: list[Entity] = []
    for entity_id in name_to_id.values():
        entity = store.get_entity(entity_id)
        assert entity is not None
        entities.append(entity)
    return entities


def _load_relations(store: SQLiteGraphStore, entities: list[Entity]) -> list:
    relations = []
    for entity in entities:
        relations.extend(store.get_entity_relations(entity.entity_id))
    return relations


def test_ontology_ingest_markdown_fixture(tmp_path: Path) -> None:
    """Ingest the ontology sample fixture and verify the full pipeline."""
    config = _build_config(tmp_path)
    store = SQLiteGraphStore(tmp_path / "graph.db")
    ontology = load_ontology(str(_ONTOLOGY_PATH))

    result = ingest_file(str(_SAMPLE_MD), graph_store=store, config=config)

    assert result["status"] == "success"
    assert isinstance(result["degraded"], bool)
    assert result["lance_row_count"] > 0
    assert result["extractor_id"] == "langextract"

    entities = _load_entities(store, result["entities"])
    assert len(entities) >= 1
    for entity in entities:
        assert entity.entity_type in ontology.entity_types
        assert entity.extractor_id == "langextract"

    relations = _load_relations(store, entities)
    assert len(relations) >= 1
    for relation in relations:
        assert relation.relation_type in ontology.relation_types

    chunks_by_id = _chunk_map(store)
    matched = False
    for entity in entities:
        start = entity.source_start_char
        end = entity.source_end_char
        chunk_id = entity.chunk_id
        if start is None or end is None or chunk_id is None:
            continue
        chunk_text = chunks_by_id[chunk_id]
        assert 0 <= start < end <= len(chunk_text)
        assert chunk_text[start:end] == entity.name
        matched = True
    assert matched


def test_entity_chunk_fk_rejected(tmp_path: Path) -> None:
    """An entity referencing a nonexistent chunk_id is rejected."""
    store = SQLiteGraphStore(tmp_path / "fk.db")
    store.add_document(
        Document(path="fk_test", source_type="text", content="x", size_bytes=1)
    )
    bad_entity = Entity(
        name="Ghost",
        entity_type="Concept",
        source_type="text",
        chunk_id="nonexistent",
    )
    with pytest.raises(ValueError, match="Chunk IDs not found"):
        store.batch_add_entities([bad_entity])
