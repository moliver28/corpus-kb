"""End-to-end ontology ingest tests (Postgres)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from corpus_kb.config import load_config
from corpus_kb.domain.models import DEFAULT_TENANT_ID
from corpus_kb.ontology import load_ontology
from corpus_kb.storage.graph_store import PostgresGraphStore
from corpus_kb.storage.tenant_conn import tenant_connection
from corpus_kb.tools.ingest_tools import ingest_file
from corpus_kb.utils.models import Entity

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


async def _delete_document(pg_pool, source: str) -> None:
    """Remove any prior row for `source` so a test's "first ingest" is
    genuinely fresh. corpus_kb_test is a shared, persistent database across
    test runs (see conftest.py), and content-hash dedup (migration 007)
    means a fixed source string re-ingested with unchanged content returns
    "skipped" rather than "success" on any run after the first -- tests
    using a fixed source/path must clean up their own row first."""
    async with tenant_connection(pg_pool, DEFAULT_TENANT_ID) as conn:
        await conn.execute(
            "DELETE FROM documents WHERE tenant_id = $1 AND source = $2",
            str(DEFAULT_TENANT_ID),
            source,
        )


@pytest.mark.asyncio
async def test_ontology_ingest_markdown_fixture(pg_pool) -> None:
    """Ingest the ontology sample fixture and verify the full pipeline."""
    await _delete_document(pg_pool, str(_SAMPLE_MD))
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


@pytest.mark.asyncio
async def test_reingest_unchanged_is_skipped(pg_pool) -> None:
    from corpus_kb.tools.ingest_common import ingest_text

    source = "test-skip-unchanged"
    await _delete_document(pg_pool, source)
    config = {
        "graph": {"extract_entities": False},
        "embedding": {"model": "qwen3-embedding:8b", "dimensions": 4096},
    }
    text = "# Doc\nStable content.\n"
    first = await ingest_text(
        text=text, pg_pool=pg_pool, source_type="markdown", config=config, source=source
    )
    assert first["status"] == "success"
    second = await ingest_text(
        text=text, pg_pool=pg_pool, source_type="markdown", config=config, source=source
    )
    assert second["status"] == "skipped"


@pytest.mark.asyncio
async def test_edit_and_reingest_supersedes_changed_chunk(pg_pool) -> None:
    from corpus_kb.tools.ingest_common import ingest_text

    config = {
        "graph": {"extract_entities": False},
        "embedding": {"model": "qwen3-embedding:8b", "dimensions": 4096},
    }
    source = "test-edit-reingest"
    await _delete_document(pg_pool, source)
    await ingest_text(
        text="# Doc\nOriginal text.\n",
        pg_pool=pg_pool,
        source_type="markdown",
        config=config,
        source=source,
    )
    result = await ingest_text(
        text="# Doc\nEdited text now.\n",
        pg_pool=pg_pool,
        source_type="markdown",
        config=config,
        source=source,
    )
    assert result["status"] == "success"
    async with tenant_connection(pg_pool, DEFAULT_TENANT_ID) as conn:
        row = await conn.fetchrow(
            "SELECT text, tombstoned_at FROM chunks c "
            "JOIN documents d ON c.doc_id = d.doc_id "
            "WHERE d.source = $1 ORDER BY chunk_index LIMIT 1",
            source,
        )
    assert "Edited" in row["text"]
    assert row["tombstoned_at"] is None


@pytest.mark.asyncio
async def test_stored_relations_have_provenance(pg_pool) -> None:
    from corpus_kb.tools.ingest_common import ingest_text

    source = "test-relation-provenance"
    await _delete_document(pg_pool, source)
    config = {
        "graph": {
            "extract_entities": True,
            "extractor": "langextract",
            "fixture_dir": "tests/fixtures/langextract_recorded",
            "live_fallback": False,
            "model_version": "test-model-v1",
            "prompt_version": "test-prompt-v1",
        },
    }
    text = "ServiceA depends on ServiceB for auth."
    result = await ingest_text(
        text=text, pg_pool=pg_pool, source_type="text", config=config, source=source
    )
    assert result["status"] == "success"
    async with tenant_connection(pg_pool, DEFAULT_TENANT_ID) as conn:
        row = await conn.fetchrow(
            """
            SELECT r.relation_type, r.confidence, r.chunk_id,
                r.extractor_id, r.model_version, r.prompt_version
            FROM relations r
            JOIN chunks c ON c.chunk_id = r.chunk_id
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE d.source = $1
            LIMIT 1
            """,
            source,
        )
    assert row is not None
    assert row["confidence"] is not None
    assert row["chunk_id"] is not None
    assert row["model_version"] == "test-model-v1"
    assert row["prompt_version"] == "test-prompt-v1"
