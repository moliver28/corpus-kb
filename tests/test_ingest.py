"""Tests for ingest tools and entity extraction (Postgres)."""

from __future__ import annotations

import pytest

from src.graph.extractor import extract_entities
from src.storage.graph_store import PostgresGraphStore
from src.tools.ingest_tools import delete_document, ingest_text
from src.utils.models import Entity


# ============================================================================
# Entity Extraction Tests
# ============================================================================


class TestEntityExtraction:
    """Test entity extraction from different source types."""

    def test_extract_entities_from_markdown_headings(self) -> None:
        markdown_text = """# Introduction
This is the introduction.

## Getting Started
Follow these steps.

### Installation
Install the package.
"""
        entities = extract_entities(markdown_text, source_type="markdown")
        assert len(entities) > 0
        entity_names = {e.name for e in entities}
        assert "Introduction" in entity_names
        assert "Getting Started" in entity_names
        assert "Installation" in entity_names

    def test_extract_entities_from_markdown_camelcase(self) -> None:
        markdown_text = """# UserService Architecture

The UserService handles authentication and UserProfile management.
The AuthManager coordinates with the TokenValidator.
"""
        entities = extract_entities(markdown_text, source_type="markdown")
        entity_names = {e.name for e in entities}
        assert "UserService" in entity_names
        assert "UserProfile" in entity_names

    def test_extract_entities_from_text_concepts(self) -> None:
        text = """
This document describes the authentication architecture.
The design pattern uses a caching strategy for performance.
"""
        entities = extract_entities(text, source_type="text")
        # Should extract at least some concept keywords
        assert len(entities) > 0

    def test_extract_entities_with_source_document_id(self) -> None:
        markdown_text = "# MyService\nA service description."
        doc_id = "doc-123"
        entities = extract_entities(
            markdown_text, source_type="markdown", source_document_id=doc_id
        )
        assert len(entities) > 0
        for entity in entities:
            assert entity.source_document_id == doc_id

    def test_extract_entities_deduplication(self) -> None:
        markdown_text = """# UserService
The UserService is important.
UserService handles authentication.
"""
        entities = extract_entities(markdown_text, source_type="markdown")
        entity_names = [e.name for e in entities]
        assert entity_names.count("UserService") == 1


# ============================================================================
# Ingest Tools Tests (async — require Postgres)
# ============================================================================


class TestIngestTools:
    """Test ingest tools with entity extraction."""

    @pytest.mark.asyncio
    async def test_ingest_text_markdown_with_entities(self, pg_pool) -> None:
        markdown_text = """# Authentication System
## Login Flow
The login process uses OAuth2.
## Token Management
Tokens are cached for performance.
"""
        config = {
            "graph": {"extract_entities": True, "backend": "postgres"},
            "database": {
                "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
            },
        }
        result = await ingest_text(
            text=markdown_text,
            pg_pool=pg_pool,
            source_type="markdown",
            config=config,
        )
        status = result["status"]
        assert isinstance(status, str)
        assert status == "success"
        entity_count = result["entity_count"]
        assert isinstance(entity_count, int)
        assert entity_count > 0
        assert "document_id" in result
        assert "entities" in result
        assert isinstance(result["entities"], dict)

    @pytest.mark.asyncio
    async def test_ingest_text_with_entity_extraction_disabled(self, pg_pool) -> None:
        markdown_text = "# MyService\nA service."
        config = {
            "graph": {"extract_entities": False, "backend": "postgres"},
            "database": {
                "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
            },
        }
        result = await ingest_text(
            text=markdown_text,
            pg_pool=pg_pool,
            source_type="markdown",
            config=config,
        )
        status = result["status"]
        assert isinstance(status, str)
        assert status == "success"
        entity_count = result["entity_count"]
        assert isinstance(entity_count, int)
        assert entity_count == 0
        assert result["entities"] == {}

    @pytest.mark.asyncio
    async def test_ingest_text_invalid_source_type(self, pg_pool) -> None:
        text = "Some text"
        config = {
            "graph": {"extract_entities": True, "backend": "postgres"},
            "database": {
                "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
            },
        }
        result = await ingest_text(
            text=text,
            pg_pool=pg_pool,
            source_type="invalid_type",
            config=config,
        )
        status = result["status"]
        assert isinstance(status, str)
        assert status == "error"
        message = result["message"]
        assert isinstance(message, str)
        assert "Invalid source_type" in message

    @pytest.mark.asyncio
    async def test_reingest_unchanged(self, pg_pool) -> None:
        """Re-ingesting the same source should upsert and not duplicate rows."""
        source = "test-reingest-unchanged"
        markdown_text = "# ReingestDoc\nContent stays the same.\n"
        config = {
            "graph": {"extract_entities": True, "backend": "postgres"},
            "database": {
                "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
            },
        }
        first = await ingest_text(
            text=markdown_text,
            pg_pool=pg_pool,
            source_type="markdown",
            config=config,
            source=source,
        )
        assert first["status"] == "success"
        doc_id = first["document_id"]

        second = await ingest_text(
            text=markdown_text,
            pg_pool=pg_pool,
            source_type="markdown",
            config=config,
            source=source,
        )
        assert second["status"] == "success"
        assert second["document_id"] == doc_id

        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT COUNT(*) AS cnt FROM documents WHERE source = $1", source
            )
            assert rows[0]["cnt"] == 1

    @pytest.mark.asyncio
    async def test_ingest_changed_file(self, pg_pool) -> None:
        """Ingesting changed content under the same source should upsert."""
        source = "test-ingest-changed"
        first_text = "# First\nOriginal content.\n"
        second_text = "# Second\nChanged content with more words.\n\nExtra paragraph.\n"
        config = {
            "graph": {"extract_entities": True, "backend": "postgres"},
            "database": {
                "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
            },
        }
        first = await ingest_text(
            text=first_text,
            pg_pool=pg_pool,
            source_type="markdown",
            config=config,
            source=source,
        )
        assert first["status"] == "success"
        doc_id = first["document_id"]

        second = await ingest_text(
            text=second_text,
            pg_pool=pg_pool,
            source_type="markdown",
            config=config,
            source=source,
        )
        status = second["status"]
        assert isinstance(status, str)
        assert status == "success"
        assert second["document_id"] == doc_id
        second_chunk_count = second["chunk_count"]
        assert isinstance(second_chunk_count, int)
        first_chunk_count = first["chunk_count"]
        assert isinstance(first_chunk_count, int)
        assert second_chunk_count != first_chunk_count or second_chunk_count > 0

    @pytest.mark.asyncio
    async def test_delete_source(self, pg_pool) -> None:
        """Deleting a document should remove it from Postgres."""
        source = "test-delete-source"
        markdown_text = "# DeleteMe\nDelete this document.\n"
        config = {
            "graph": {"extract_entities": True, "backend": "postgres"},
            "database": {
                "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
            },
        }
        result = await ingest_text(
            text=markdown_text,
            pg_pool=pg_pool,
            source_type="markdown",
            config=config,
            source=source,
        )
        assert result["status"] == "success"
        doc_id = result["document_id"]

        delete_result = await delete_document(doc_id, pg_pool)
        assert delete_result["status"] == "success"

        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT COUNT(*) AS cnt FROM documents WHERE doc_id = $1", doc_id
            )
            assert rows[0]["cnt"] == 0


# ============================================================================
# Graph Store Tests (async — require Postgres)
# ============================================================================


class TestGraphStore:
    """Test graph store operations via PostgresGraphStore."""

    @pytest.mark.asyncio
    async def test_graph_store_add_entity(self, pg_pool) -> None:
        store = PostgresGraphStore(pg_pool)
        entity = Entity(
            name="TestService",
            entity_type="CLASS",
            source_type="code",
            source_document_id="doc-123",
        )
        entity_id = await store.add_entity(entity)
        assert entity_id is not None
        retrieved = await store.get_entity(entity_id)
        assert retrieved is not None
        assert retrieved.name == "TestService"

    @pytest.mark.asyncio
    async def test_graph_store_search_entities(self, pg_pool) -> None:
        store = PostgresGraphStore(pg_pool)
        e1 = Entity(name="SearchServiceTest1", entity_type="CLASS", source_type="code")
        e2 = Entity(name="SearchServiceTest2", entity_type="CLASS", source_type="code")
        await store.add_entity(e1)
        await store.add_entity(e2)
        results = await store.search_entities("SearchServiceTest")
        assert len(results) >= 2
