"""Domain command, query, and result models (Pydantic v2).

These are the serialization envelopes that flow through command handlers,
query handlers, and protocol adapters. They are NOT domain aggregates
(aggregates live in aggregates.py and use the eventsourcing library).
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _DEFAULT_TENANT_ID_STR

# The canonical sentinel string lives in corpus_kb.contracts.model.
DEFAULT_TENANT_ID = UUID(_DEFAULT_TENANT_ID_STR)


# -- Commands ---------------------------------------------------------


class DomainCommand(BaseModel):
    """Base class for all commands. Carries tenant context + dedup key."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    command_id: UUID = Field(default_factory=uuid4)


class IngestFileCommand(DomainCommand):
    """Ingest a file from disk."""

    file_path: str
    content: str | None = None
    source_type: str | None = None
    # Wire name is ``validate`` (the surface param); the field carries the
    # same value. Named validate_mode here: BaseModel already uses `validate`.
    # None means "the caller did not say": the active profile's ``validate``
    # value then decides (Wave 6); with no profile it resolves to ``off``.
    validate_mode: str | None = None


class IngestTextCommand(DomainCommand):
    """Ingest raw text with a synthetic source name."""

    text: str
    source: str
    source_type: str = "text"
    validate_mode: str | None = None


class IngestDirectoryCommand(DomainCommand):
    """Recursively ingest all files in a directory."""

    directory_path: str
    recursive: bool = True
    validate_mode: str | None = None


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


# -- Queries ----------------------------------------------------------


class SearchQuery(BaseModel):
    """Hybrid vector + FTS search query.

    ``k=None`` (caller omitted k) resolves to ``reranking.return_top_k``
    in the QueryHandler (W3 contract); an explicit k always wins.
    """

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    query: str
    k: int | None = None
    source_type: str | None = None
    self_query: bool | None = None  # None -> use config default


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
    entity_type: str | None = None
    limit: int = 100
    offset: int = 0


class ListDocumentChunksQuery(BaseModel):
    """List one document's chunks, one window (Phase-2 Wave 5)."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    doc_id: UUID
    limit: int = 100
    offset: int = 0


class ListRelationsQuery(BaseModel):
    """List graph relations, one window (Phase-2 Wave 6 Map)."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    limit: int = 100
    offset: int = 0


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


class VerifyAnswerQuery(BaseModel):
    """Verify an LLM-generated answer's claims against cited chunks."""

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    answer: str
    chunk_ids: list[UUID] = Field(default_factory=list)


class RoutedQuery(BaseModel):
    """Query routed to the most appropriate retrieval strategy.

    ``k=None`` (caller omitted k) resolves to ``reranking.return_top_k``
    when the router takes the vector path (same contract as SearchQuery).
    """

    tenant_id: UUID = Field(default=DEFAULT_TENANT_ID)
    query: str
    k: int | None = None


# -- Results ----------------------------------------------------------


# -- Results (re-exported from domain/results.py, the 250-line rule) --


from corpus_kb.domain.results import ClaimVerdict as ClaimVerdict  # noqa: E402
from corpus_kb.domain.results import DocumentChunkResult as DocumentChunkResult  # noqa: E402
from corpus_kb.domain.results import DocumentResult as DocumentResult  # noqa: E402
from corpus_kb.domain.results import EntityResult as EntityResult  # noqa: E402
from corpus_kb.domain.results import RelationListResult as RelationListResult  # noqa: E402
from corpus_kb.domain.results import RouteDecision as RouteDecision  # noqa: E402
from corpus_kb.domain.results import RoutedResult as RoutedResult  # noqa: E402
from corpus_kb.domain.results import SearchResult as SearchResult  # noqa: E402
from corpus_kb.domain.results import VerifyAnswerResult as VerifyAnswerResult  # noqa: E402

__all__ = [
    "AddEntityCommand",
    "AddRelationCommand",
    "ClaimVerdict",
    "DeleteDocumentCommand",
    "DocumentChunkResult",
    "DocumentResult",
    "DomainCommand",
    "EntityResult",
    "IngestDirectoryCommand",
    "IngestFileCommand",
    "IngestTextCommand",
    "ListDocumentChunksQuery",
    "ListDocumentsQuery",
    "ListEntitiesQuery",
    "ListRelationsQuery",
    "RelationListResult",
    "RouteDecision",
    "RoutedQuery",
    "RoutedResult",
    "SQLQuery",
    "SearchContextQuery",
    "SearchQuery",
    "SearchResult",
    "SearchSimilarQuery",
    "VerifyAnswerQuery",
    "VerifyAnswerResult",
]
