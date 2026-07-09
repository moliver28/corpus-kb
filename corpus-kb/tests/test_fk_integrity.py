"""FK integrity tests for single add_entity/add_relation paths."""

from __future__ import annotations

import sqlite3

import pytest

from src.storage.graph_store import SQLiteGraphStore
from src.utils.models import Entity, Relation


def test_add_entity_nonexistent_chunk_id_raises() -> None:
    """Single add_entity with a nonexistent chunk_id raises ValueError."""
    store = SQLiteGraphStore(":memory:")
    entity = Entity(
        name="TestEntity",
        entity_type="Concept",
        source_type="code",
        chunk_id="nonexistent-chunk-id",
    )
    with pytest.raises(ValueError, match="Chunk IDs not found"):
        store.add_entity(entity)


def test_add_entity_valid_chunk_id_succeeds() -> None:
    """Single add_entity with a valid chunk_id succeeds."""
    store = SQLiteGraphStore(":memory:")
    # First add a document and chunk so the chunk_id exists
    from src.utils.models import Chunk, Document

    doc = Document(path="test.txt", source_type="text", content="hello", size_bytes=5)
    store.add_document(doc)
    chunk = Chunk(
        chunk_id="chunk-1",
        document_id=doc.document_id,
        text="hello",
        source_type="text",
    )
    store.add_chunk(chunk)

    entity = Entity(
        name="TestEntity",
        entity_type="Concept",
        source_type="code",
        chunk_id="chunk-1",
    )
    result = store.add_entity(entity)
    assert result == entity.entity_id


def test_add_relation_nonexistent_entity_raises() -> None:
    """Single add_relation with a nonexistent source_entity_id raises IntegrityError."""
    store = SQLiteGraphStore(":memory:")
    relation = Relation(
        source_entity_id="nonexistent-entity",
        target_entity_id="also-nonexistent",
        relation_type="MENTIONS",
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.add_relation(relation)


def test_add_relation_valid_entities_succeeds() -> None:
    """Single add_relation between two existing entities succeeds."""
    store = SQLiteGraphStore(":memory:")
    e1 = Entity(name="EntityA", entity_type="Concept", source_type="code")
    e2 = Entity(name="EntityB", entity_type="Concept", source_type="code")
    store.add_entity(e1)
    store.add_entity(e2)

    relation = Relation(
        source_entity_id=e1.entity_id,
        target_entity_id=e2.entity_id,
        relation_type="RELATED_TO",
    )
    result = store.add_relation(relation)
    assert result == relation.relation_id
