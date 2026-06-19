"""Tests for ingest tools and entity extraction.

Test suite for markdown/text entity extraction in the ingest pipeline.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from src.graph.extractor import extract_entities
from src.storage.graph_store import SQLiteGraphStore
from src.tools.ingest_tools import ingest_text
from src.utils.models import Entity


# ============================================================================
# Entity Extraction Tests
# ============================================================================


class TestEntityExtraction:
    """Test entity extraction from different source types."""

    def test_extract_entities_from_markdown_headings(self) -> None:
        """Given markdown with headings, when extract_entities is called, then headings are extracted as CONCEPT entities."""
        # Given
        markdown_text = """# Introduction
This is the introduction.

## Getting Started
Follow these steps.

### Installation
Install the package.
"""

        # When
        entities = extract_entities(markdown_text, source_type="markdown")

        # Then
        assert len(entities) > 0
        entity_names = {e.name for e in entities}
        assert "Introduction" in entity_names
        assert "Getting Started" in entity_names
        assert "Installation" in entity_names

        # All should be CONCEPT type
        for entity in entities:
            if entity.name in {"Introduction", "Getting Started", "Installation"}:
                assert entity.entity_type == "CONCEPT"
                assert entity.source_type == "markdown"

    def test_extract_entities_from_markdown_camelcase(self) -> None:
        """Given markdown with CamelCase identifiers, when extract_entities is called, then identifiers are extracted."""
        # Given
        markdown_text = """# UserService Architecture

The UserService handles authentication and UserProfile management.
The AuthManager coordinates with the TokenValidator.
"""

        # When
        entities = extract_entities(markdown_text, source_type="markdown")

        # Then
        entity_names = {e.name for e in entities}
        assert "UserService" in entity_names
        assert "UserProfile" in entity_names
        assert "AuthManager" in entity_names
        assert "TokenValidator" in entity_names

    def test_extract_entities_from_text_concepts(self) -> None:
        """Given plain text with concept keywords, when extract_entities is called, then concepts are extracted."""
        # Given
        text = """
This document describes the authentication architecture.
The design pattern uses a caching strategy for performance.
The API protocol follows REST standards.
"""

        # When
        entities = extract_entities(text, source_type="text")

        # Then
        entity_names = {e.name for e in entities}
        # Should extract concept keywords
        assert any("Authentication" in name or "Caching" in name or "Api" in name for name in entity_names)

    def test_extract_entities_with_source_document_id(self) -> None:
        """Given markdown and a source_document_id, when extract_entities is called, then entities include the document ID."""
        # Given
        markdown_text = "# MyService\nA service description."
        doc_id = "doc-123"

        # When
        entities = extract_entities(markdown_text, source_type="markdown", source_document_id=doc_id)

        # Then
        assert len(entities) > 0
        for entity in entities:
            assert entity.source_document_id == doc_id

    def test_extract_entities_deduplication(self) -> None:
        """Given markdown with repeated entity names, when extract_entities is called, then duplicates are removed."""
        # Given
        markdown_text = """# UserService
The UserService is important.
UserService handles authentication.
"""

        # When
        entities = extract_entities(markdown_text, source_type="markdown")

        # Then
        entity_names = [e.name for e in entities]
        # Should not have duplicate "UserService"
        assert entity_names.count("UserService") == 1


# ============================================================================
# Ingest Tools Tests
# ============================================================================


class TestIngestTools:
    """Test ingest tools with entity extraction."""

    def test_ingest_text_markdown_with_entities(self) -> None:
        """Given markdown text, when ingest_text is called, then entities are extracted and added to graph."""
        # Given
        markdown_text = """# Authentication System
## Login Flow
The login process uses OAuth2.
## Token Management
Tokens are cached for performance.
"""
        config = {
            "graph": {"extract_entities": True, "backend": "sqlite"},
            "storage": {"graph_db": ":memory:"},
        }

        # When
        result = ingest_text(
            text=markdown_text,
            source_type="markdown",
            config=config,
        )

        # Then
        assert result["status"] == "success"
        assert result["entity_count"] > 0
        assert "document_id" in result
        assert "entities" in result
        assert isinstance(result["entities"], dict)

    def test_ingest_text_with_entity_extraction_disabled(self) -> None:
        """Given markdown text with extract_entities=false, when ingest_text is called, then no entities are extracted."""
        # Given
        markdown_text = "# MyService\nA service."
        config = {
            "graph": {"extract_entities": False, "backend": "sqlite"},
            "storage": {"graph_db": ":memory:"},
        }

        # When
        result = ingest_text(
            text=markdown_text,
            source_type="markdown",
            config=config,
        )

        # Then
        assert result["status"] == "success"
        assert result["entity_count"] == 0
        assert result["entities"] == {}

    def test_ingest_text_plain_text(self) -> None:
        """Given plain text, when ingest_text is called with source_type='text', then entities are extracted."""
        # Given
        text = """
The authentication system uses JWT tokens.
The caching layer improves performance.
The API gateway routes requests.
"""
        config = {
            "graph": {"extract_entities": True, "backend": "sqlite"},
            "storage": {"graph_db": ":memory:"},
        }

        # When
        result = ingest_text(
            text=text,
            source_type="text",
            config=config,
        )

        # Then
        assert result["status"] == "success"
        assert "document_id" in result
        assert result["source_type"] == "text"

    def test_ingest_text_invalid_source_type(self) -> None:
        """Given invalid source_type, when ingest_text is called, then error is returned."""
        # Given
        text = "Some text"
        config = {
            "graph": {"extract_entities": True, "backend": "sqlite"},
            "storage": {"graph_db": ":memory:"},
        }

        # When
        result = ingest_text(
            text=text,
            source_type="invalid_type",
            config=config,
        )

        # Then
        assert result["status"] == "error"
        assert "Invalid source_type" in result["message"]


# ============================================================================
# Graph Store Tests
# ============================================================================


class TestGraphStore:
    """Test graph store operations."""

    def test_graph_store_add_entity(self) -> None:
        """Given an entity, when add_entity is called, then entity is stored and retrievable."""
        # Given
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            graph_store = SQLiteGraphStore(db_path)

            entity = Entity(
                name="TestService",
                entity_type="CLASS",
                source_type="code",
                source_document_id="doc-123",
            )

            # When
            entity_id = graph_store.add_entity(entity)

            # Then
            assert entity_id == entity.entity_id
            retrieved = graph_store.get_entity(entity_id)
            assert retrieved is not None
            assert retrieved.name == "TestService"
            assert retrieved.entity_type == "CLASS"

    def test_graph_store_search_entities(self) -> None:
        """Given entities in the store, when search_entities is called, then matching entities are returned."""
        # Given
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            graph_store = SQLiteGraphStore(db_path)

            entity1 = Entity(
                name="UserService",
                entity_type="CLASS",
                source_type="code",
            )
            entity2 = Entity(
                name="AuthService",
                entity_type="CLASS",
                source_type="code",
            )

            graph_store.add_entity(entity1)
            graph_store.add_entity(entity2)

            # When
            results = graph_store.search_entities("Service")

            # Then
            assert len(results) == 2
            names = {e.name for e in results}
            assert "UserService" in names
            assert "AuthService" in names

    def test_graph_store_search_entities_by_type(self) -> None:
        """Given entities of different types, when search_entities is called with type filter, then only matching type is returned."""
        # Given
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            graph_store = SQLiteGraphStore(db_path)

            entity1 = Entity(
                name="UserService",
                entity_type="CLASS",
                source_type="code",
            )
            entity2 = Entity(
                name="authenticate",
                entity_type="FUNCTION",
                source_type="code",
            )

            graph_store.add_entity(entity1)
            graph_store.add_entity(entity2)

            # When
            results = graph_store.search_entities("Service", entity_type="CLASS")

            # Then
            assert len(results) == 1
            assert results[0].name == "UserService"


# ============================================================================
# Integration Tests
# ============================================================================


class TestIngestIntegration:
    """Integration tests for the full ingest pipeline."""

    def test_markdown_entities_extracted_end_to_end(self) -> None:
        """Given markdown file, when ingested, then entities are extracted and stored in graph."""
        # Given
        markdown_content = """# API Documentation

## Authentication
The authentication system uses JWT tokens.

## Endpoints
### GET /users
Retrieve all users.

### POST /users
Create a new user.

## Error Handling
The API returns standard HTTP error codes.
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a temporary markdown file
            md_file = Path(tmpdir) / "api.md"
            md_file.write_text(markdown_content)

            # Create graph store
            db_path = Path(tmpdir) / "graph.db"
            graph_store = SQLiteGraphStore(db_path)

            config = {
                "graph": {"extract_entities": True, "backend": "sqlite"},
                "storage": {"graph_db": str(db_path)},
            }

            # When
            result = ingest_text(
                text=markdown_content,
                source_type="markdown",
                graph_store=graph_store,
                config=config,
            )

            # Then
            assert result["status"] == "success"
            assert result["entity_count"] > 0

            # Verify entities are in graph store
            entities = graph_store.search_entities("Authentication")
            assert len(entities) > 0

            entities = graph_store.search_entities("Endpoints")
            assert len(entities) > 0
