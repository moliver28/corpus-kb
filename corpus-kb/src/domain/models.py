"""Domain command, query, and result models (Pydantic v2).

These are the serialization envelopes that flow through command handlers,
query handlers, and protocol adapters. They are NOT domain aggregates
(aggregates live in aggregates.py and use the eventsourcing library).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ============================================================================
# Default tenant (single-tenant placeholder)
# ============================================================================

DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ============================================================================
# Commands
# ============================================================================


class DomainCommand(BaseModel):
    """Base class for all commands. Carries tenant context + dedup key."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    command_id: UUID = Field(default_factory=uuid4)


class IngestFileCommand(DomainCommand):
    """Ingest a file from disk."""

    file_path: str
    content: Optional[str] = None
    source_type: Optional[str] = None


class IngestTextCommand(DomainCommand):
    """Ingest raw text with a synthetic source name."""

    text: str
    source: str
    source_type: str = "text"


class IngestDirectoryCommand(DomainCommand):
    """Recursively ingest all files in a directory."""

    directory_path: str
    recursive: bool = True


class AddEntityCommand(DomainCommand):
    """Add an entity to the knowledge graph."""

    name: str
    entity_type: str = "concept"
    metadata: dict[str, object] = Field(default_factory=dict)


class AddRelationCommand(DomainCommand):
    """Add a relation between two entities."""

    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str = "related_to"
    weight: float = 1.0
    metadata: dict[str, object] = Field(default_factory=dict)


class DeleteDocumentCommand(DomainCommand):
    """Delete a document and its chunks."""

    doc_id: UUID


# ============================================================================
# Queries
# ============================================================================


class SearchQuery(BaseModel):
    """Hybrid vector + FTS search query."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    query: str
    k: int = 10
    source_type: Optional[str] = None


class SQLQuery(BaseModel):
    """Raw SQL query (read-only)."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    sql: str
    params: dict[str, object] = Field(default_factory=dict)


class ListDocumentsQuery(BaseModel):
    """List documents with pagination."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    limit: int = 100
    offset: int = 0


class ListEntitiesQuery(BaseModel):
    """List entities, optionally filtered by type."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    entity_type: Optional[str] = None
    limit: int = 100


class SearchSimilarQuery(BaseModel):
    """Find chunks similar to a given chunk via vector distance."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    chunk_id: UUID
    k: int = 10


class SearchContextQuery(BaseModel):
    """Search with surrounding context chunks."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    query: str
    k: int = 5
    context_chunks: int = 2


# ============================================================================
# Results
# ============================================================================


class SearchResult(BaseModel):
    """A single search hit."""

    chunk_id: UUID
    text: str
    score: float
    source: str
    doc_id: UUID


class DocumentResult(BaseModel):
    """Document metadata in list responses."""

    doc_id: UUID
    source: str
    source_type: str
    chunk_count: int
    created_at: str


class EntityResult(BaseModel):
    """Entity in list/search responses."""

    entity_id: UUID
    name: str
    entity_type: str
    metadata: dict[str, object] = Field(default_factory=dict)
