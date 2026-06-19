"""Pydantic data models for Corpus-KB.

Chunk, Document, SearchResult, Entity, Relation, Version, Branch, Stats.
Pure dataclasses with serialization methods for LanceDB round-tripping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Core Data Models
# ============================================================================


class Chunk(BaseModel):
    """A chunk of text from a document."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "chunk_id": "chunk-123",
                "document_id": "doc-456",
                "text": "def hello(): pass",
                "source_type": "code",
                "entity_name": "hello",
                "entity_type": "function",
            }
        },
    )

    chunk_id: str = Field(default_factory=lambda: str(uuid4()))
    document_id: str
    text: str
    embedding: Optional[list[float]] = None
    source_type: str  # "code" | "markdown" | "text"
    entity_name: Optional[str] = None  # For code chunks: function/class name
    entity_type: Optional[str] = None  # For code chunks: "function" | "class" | "module"
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    parent_chunk_id: Optional[str] = None
    sibling_order: Optional[int] = None
    sibling_count: Optional[int] = None
    heading_path: Optional[list[str]] = None  # For markdown: ["# Title", "## Section"]
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Document(BaseModel):
    """A document (file or raw text)."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "document_id": "doc-456",
                "path": "/path/to/file.py",
                "source_type": "code",
                "content": "...",
                "size_bytes": 1024,
            }
        },
    )

    document_id: str = Field(default_factory=lambda: str(uuid4()))
    path: str  # File path or "raw_text"
    source_type: str  # "code" | "markdown" | "text"
    content: str
    size_bytes: int
    chunk_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SearchResult(BaseModel):
    """A search result."""

    chunk_id: str
    document_id: str
    text: str
    score: float
    source_type: str
    entity_name: Optional[str] = None
    parent_chunk_id: Optional[str] = None
    sibling_order: Optional[int] = None
    heading_path: Optional[list[str]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Graph Models
# ============================================================================


class Entity(BaseModel):
    """An entity in the knowledge graph."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "entity_id": "ent-123",
                "name": "UserService",
                "entity_type": "CLASS",
                "source_type": "code",
                "source_document_id": "doc-456",
            }
        },
    )

    entity_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    entity_type: str  # "CONCEPT" | "CLASS" | "FUNCTION" | "MODULE" | "PERSON" | "PLACE"
    source_type: str  # "code" | "markdown" | "text"
    source_document_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Relation(BaseModel):
    """A relation between two entities."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "relation_id": "rel-123",
                "source_entity_id": "ent-123",
                "target_entity_id": "ent-456",
                "relation_type": "CALLS",
            }
        },
    )

    relation_id: str = Field(default_factory=lambda: str(uuid4()))
    source_entity_id: str
    target_entity_id: str
    relation_type: str  # "CALLS" | "DEPENDS_ON" | "CONTAINS" | "REFERENCES"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Versioning Models
# ============================================================================


class Version(BaseModel):
    """A version of the database."""

    version_id: str
    timestamp: datetime
    tag: Optional[str] = None
    description: Optional[str] = None


class Branch(BaseModel):
    """A branch of the database."""

    branch_id: str
    name: str
    created_at: datetime
    head_version: str


# ============================================================================
# Stats Models
# ============================================================================


class Stats(BaseModel):
    """Database statistics."""

    total_documents: int
    total_chunks: int
    total_entities: int
    total_relations: int
    storage_size_bytes: int
    last_updated: datetime
