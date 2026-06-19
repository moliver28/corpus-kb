#!/usr/bin/env python3
"""Verification script for entity extraction implementation."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.graph.extractor import extract_entities
from src.storage.graph_store import SQLiteGraphStore
from src.tools.ingest_tools import ingest_text
from src.utils.models import Entity


def test_entity_extraction():
    """Test entity extraction from markdown."""
    print("=" * 60)
    print("TEST 1: Entity Extraction from Markdown")
    print("=" * 60)

    markdown_text = """# Introduction
This is the introduction.

## Getting Started
Follow these steps.

### Installation
Install the package.
"""

    entities = extract_entities(markdown_text, source_type="markdown")
    print(f"✓ Extracted {len(entities)} entities from markdown")
    for entity in entities:
        print(f"  - {entity.name} ({entity.entity_type})")

    assert len(entities) > 0, "No entities extracted!"
    assert any(e.name == "Introduction" for e in entities), "Introduction not found!"
    print("✓ PASS: Markdown entity extraction works\n")


def test_graph_store():
    """Test graph store operations."""
    print("=" * 60)
    print("TEST 2: Graph Store Operations")
    print("=" * 60)

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        graph_store = SQLiteGraphStore(db_path)

        entity = Entity(
            name="TestService",
            entity_type="CLASS",
            source_type="code",
            source_document_id="doc-123",
        )

        entity_id = graph_store.add_entity(entity)
        print(f"✓ Added entity: {entity.name} (ID: {entity_id})")

        retrieved = graph_store.get_entity(entity_id)
        assert retrieved is not None, "Entity not retrieved!"
        assert retrieved.name == "TestService", "Entity name mismatch!"
        print(f"✓ Retrieved entity: {retrieved.name}")

        print("✓ PASS: Graph store operations work\n")


def test_ingest_text():
    """Test ingest_text with entity extraction."""
    print("=" * 60)
    print("TEST 3: Ingest Text with Entity Extraction")
    print("=" * 60)

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

    result = ingest_text(
        text=markdown_text,
        source_type="markdown",
        config=config,
    )

    print(f"✓ Ingest result: {result['status']}")
    print(f"✓ Document ID: {result['document_id']}")
    print(f"✓ Entity count: {result['entity_count']}")
    print(f"✓ Entities: {result['entities']}")

    assert result["status"] == "success", "Ingest failed!"
    assert result["entity_count"] > 0, "No entities extracted during ingest!"
    print("✓ PASS: Ingest with entity extraction works\n")


def test_ingest_text_disabled():
    """Test ingest_text with entity extraction disabled."""
    print("=" * 60)
    print("TEST 4: Ingest Text with Entity Extraction Disabled")
    print("=" * 60)

    markdown_text = "# MyService\nA service."
    config = {
        "graph": {"extract_entities": False, "backend": "sqlite"},
        "storage": {"graph_db": ":memory:"},
    }

    result = ingest_text(
        text=markdown_text,
        source_type="markdown",
        config=config,
    )

    print(f"✓ Ingest result: {result['status']}")
    print(f"✓ Entity count: {result['entity_count']}")

    assert result["status"] == "success", "Ingest failed!"
    assert result["entity_count"] == 0, "Entities extracted when disabled!"
    print("✓ PASS: Entity extraction can be disabled\n")


if __name__ == "__main__":
    try:
        test_entity_extraction()
        test_graph_store()
        test_ingest_text()
        test_ingest_text_disabled()

        print("=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
