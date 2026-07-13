"""Domain aggregates using the eventsourcing library.

Each aggregate encapsulates state and emits events via the @event decorator.
The eventsourcing library handles versioning, snapshotting, and persistence.

Event flow:
    Command → Aggregate method (@event) → Event emitted → app.save() → Event store

Projections subscribe to events and update read models (documents, chunks, vectors).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from eventsourcing.domain import Aggregate, event


# ============================================================================
# Document Aggregate
# ============================================================================


@dataclass
class Document(Aggregate):
    """Aggregate root for an ingested document.

    Events:
        Ingested: fired on creation (source, source_type, metadata)
        ChunksAdded: fired when chunks are added (chunk_count, chunk_texts)
        Deleted: fired when document is deleted
    """

    tenant_id: UUID
    source: str
    source_type: str = "text"
    chunk_count: int = 0
    file_size: Optional[int] = None
    file_hash: Optional[str] = None
    language: Optional[str] = None
    metadata: dict[str, object] = field(default_factory=dict)

    @event("Ingested")
    def __init__(
        self,
        tenant_id: UUID,
        source: str,
        source_type: str = "text",
        file_size: Optional[int] = None,
        file_hash: Optional[str] = None,
        language: Optional[str] = None,
        metadata: Optional[dict[str, object]] = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.source = source
        self.source_type = source_type
        self.file_size = file_size
        self.file_hash = file_hash
        self.language = language
        self.metadata = metadata or {}
        self.chunk_count = 0

    @event("ChunksAdded")
    def add_chunks(
        self,
        chunk_count: int,
        chunk_texts: list[str],
    ) -> None:
        """Add chunks to the document. Projection handles embedding."""
        self.chunk_count += chunk_count

    @event("Deleted")
    def delete(self) -> None:
        """Mark document as deleted."""
        self._deleted = True


# ============================================================================
# Entity Aggregate
# ============================================================================


@dataclass
class Entity(Aggregate):
    """Aggregate for a knowledge graph entity (function, class, concept, etc.).

    Events:
        Created: fired on creation (name, entity_type, metadata)
    """

    tenant_id: UUID
    name: str
    entity_type: str = "concept"
    metadata: dict[str, object] = field(default_factory=dict)

    @event("Created")
    def __init__(
        self,
        tenant_id: UUID,
        name: str,
        entity_type: str = "concept",
        metadata: Optional[dict[str, object]] = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.name = name
        self.entity_type = entity_type
        self.metadata = metadata or {}


# ============================================================================
# Relation Aggregate
# ============================================================================


@dataclass
class Relation(Aggregate):
    """Aggregate for a knowledge graph relation (edge between entities).

    Events:
        Created: fired on creation (source_entity_id, target_entity_id, relation_type)
    """

    tenant_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str = "related_to"
    weight: float = 1.0
    metadata: dict[str, object] = field(default_factory=dict)

    @event("Created")
    def __init__(
        self,
        tenant_id: UUID,
        source_entity_id: UUID,
        target_entity_id: UUID,
        relation_type: str = "related_to",
        weight: float = 1.0,
        metadata: Optional[dict[str, object]] = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.source_entity_id = source_entity_id
        self.target_entity_id = target_entity_id
        self.relation_type = relation_type
        self.weight = weight
        self.metadata = metadata or {}
