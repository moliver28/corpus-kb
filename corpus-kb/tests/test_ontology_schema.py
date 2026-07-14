"""Contract/schema tests for ontology ingestion foundation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.ontology import load_ontology


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


class TestConfig:
    def test_config_yaml_exists_with_required_keys(self) -> None:
        config_path = Path("config.yaml")
        assert config_path.exists()

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        embedding = config.get("embedding", {})
        database = config.get("database", {})
        graph = config.get("graph", {})

        assert embedding.get("model") == "nomic-embed-text"
        assert embedding.get("dimensions") == 768
        assert database.get("connection_string") is not None
        assert graph.get("extractor") == "langextract"
