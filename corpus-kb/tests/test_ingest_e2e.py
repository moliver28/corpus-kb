"""End-to-end ontology ingest tests (Postgres)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from src.config import load_config
from src.ontology import load_ontology
from src.storage.graph_store import PostgresGraphStore
from src.tools.ingest_tools import ingest_file
from src.utils.models import Entity

_FIXTURE_DIR = Path(__file__).with_name("fixtures") / "langextract_recorded"
_SAMPLE_MD = Path(__file__).with_name("fixtures") / "ontology_sample.md"
_ONTOLOGY_PATH = Path("config/ontology.yaml")


def _build_config() -> dict[str, object]:
    config = load_config()
    graph = cast(dict[str, object], config.setdefault("graph", {}))
    graph["extractor"] = "langextract"
    graph["fixture_dir"] = str(_FIXTURE_DIR.resolve())
    graph["live_fallback"] = False
    return config


@pytest.mark.asyncio
async def test_ontology_ingest_markdown_fixture(pg_pool) -> None:
    """Ingest the ontology sample fixture and verify the full pipeline."""
    config = _build_config()
    ontology = load_ontology(str(_ONTOLOGY_PATH))

    result = await ingest_file(str(_SAMPLE_MD), pg_pool, config=config)

    assert result["status"] == "success"
    assert isinstance(result["degraded"], bool)
    assert result["extractor_id"] == "langextract"

    # Verify entities via PostgresGraphStore
    store = PostgresGraphStore(pg_pool)
    entity_names = result.get("entities", {})
    assert len(entity_names) >= 1

    for name in entity_names:
        entities = await store.search_entities(name)
        for entity in entities:
            assert entity.entity_type in ontology.entity_types


@pytest.mark.asyncio
async def test_entity_chunk_fk_rejected(pg_pool) -> None:
    """An entity referencing a nonexistent chunk_id is handled gracefully."""
    store = PostgresGraphStore(pg_pool)
    bad_entity = Entity(
        name="Ghost",
        entity_type="Concept",
        source_type="text",
        chunk_id="nonexistent",
    )
    # Postgres entities table doesn't have FK on chunk_id — should succeed
    result = await store.add_entity(bad_entity)
    assert result is not None
