"""Tests for ingest tools and entity extraction (Postgres)."""

from __future__ import annotations

import pytest

from src.graph.extractor import extract_entities
from src.storage.graph_store import PostgresGraphStore
from src.tools.ingest_tools import ingest_text
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
        entity_names = {e.name for e in entities}
        assert any(
            "Authentication" in name or "Caching" in name for name in entity_names
        )

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
        assert result["status"] == "success"
        assert result["entity_count"] > 0
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
        assert result["status"] == "success"
        assert result["entity_count"] == 0
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
        assert result["status"] == "error"
        assert "Invalid source_type" in result["message"]


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
