"""Contract/schema tests for Wave 1 ontology ingestion foundation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from src.ontology import load_ontology
from src.storage.graph_store import SQLiteGraphStore
from src.utils.models import Entity


_PROVENANCE_COLUMNS = {
    "chunk_id",
    "source_start_char",
    "source_end_char",
    "confidence",
    "extractor_id",
}


def _table_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


class TestOntologyLoader:
    _EXPECTED_ENTITY_TYPES = [
        "Document",
        "Section",
        "Chunk",
        "Person",
        "Org",
        "Product",
        "Concept",
        "Claim",
        "Metric",
    ]

    _EXPECTED_RELATION_TYPES = [
        "PART_OF",
        "MENTIONS",
        "DEFINED_AS",
        "AUTHORED_BY",
        "CITES",
        "SUPPORTS",
        "CONTRADICTS",
        "RELATED_TO",
        "INSTANCE_OF",
    ]

    def test_load_default_ontology_has_exact_plan_types(self) -> None:
        ontology = load_ontology("config/ontology.yaml")
        assert ontology.entity_types == self._EXPECTED_ENTITY_TYPES
        assert ontology.relation_types == self._EXPECTED_RELATION_TYPES
        assert set(ontology.entity_types).isdisjoint(ontology.relation_types)

    def test_load_malformed_ontology_empty_relation_types_raises(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("entity_types:\n  - Class\nrelation_types: []\n")
        with pytest.raises(ValueError):
            load_ontology(path)


class TestGraphSchemaMigration:
    def test_fresh_db_migration_adds_columns_idempotently(self, tmp_path: Path) -> None:
        db_path = tmp_path / "fresh.db"
        assert not _table_columns(db_path, "entities") & _PROVENANCE_COLUMNS
        assert not _table_columns(db_path, "relations") & _PROVENANCE_COLUMNS

        store = SQLiteGraphStore(db_path)
        store.close()

        assert _PROVENANCE_COLUMNS <= _table_columns(db_path, "entities")
        assert _PROVENANCE_COLUMNS <= _table_columns(db_path, "relations")

        # Re-opening must be idempotent (no duplicate columns / no error).
        store2 = SQLiteGraphStore(db_path)
        store2.close()

        assert _PROVENANCE_COLUMNS <= _table_columns(db_path, "entities")
        assert _PROVENANCE_COLUMNS <= _table_columns(db_path, "relations")


class TestGraphBatchOperations:
    def test_batch_add_entities_inserts_three_rows(self, tmp_path: Path) -> None:
        db_path = tmp_path / "batch.db"
        store = SQLiteGraphStore(db_path)
        entities = [
            Entity(name="Alpha", entity_type="Class", source_type="code"),
            Entity(name="Beta", entity_type="Function", source_type="code"),
            Entity(name="Gamma", entity_type="Method", source_type="code"),
        ]
        ids = store.batch_add_entities(entities)

        assert len(ids) == 3
        assert len(store.search_entities("Alpha")) == 1
        assert len(store.search_entities("Beta")) == 1
        assert len(store.search_entities("Gamma")) == 1


class TestConfig:
    def test_config_yaml_exists_with_required_keys(self) -> None:
        config_path = Path("config.yaml")
        assert config_path.exists()

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        embedding = config.get("embedding", {})
        storage = config.get("storage", {})
        graph = config.get("graph", {})

        assert embedding.get("model") == "nomic-embed-text"
        assert embedding.get("dimensions") == 768
        assert storage.get("lancedb_uri") is not None
        assert graph.get("extractor") == "langextract"
