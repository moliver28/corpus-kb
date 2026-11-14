"""The read-side RESULT models (the ``domain/models`` split, 250-line rule).

Moved out of ``domain/models.py``: the relations-list, entity, verify,
and routed-composite result shapes. ``domain/models.py`` re-exports
them, so every existing ``from corpus_kb.domain.models import X``
import path stays byte-identical.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class RelationListResult(BaseModel):
    """One relation row in the relations-list window (Wave 6 Map)."""

    relation_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str
    weight: float
    source_name: str
    target_name: str


class EntityResult(BaseModel):
    """Entity in list/search responses."""

    entity_id: UUID
    name: str
    entity_type: str
    metadata: dict[str, object] = Field(default_factory=dict)


class ClaimVerdict(BaseModel):
    """Verdict for a single claim extracted from an answer."""

    claim: str
    label: str
    confidence: float
    cited_chunk_ids: list[UUID]


class VerifyAnswerResult(BaseModel):
    """Result of verifying an answer's claims against cited chunks."""

    verdicts: list[ClaimVerdict]
    groundedness: float
    abstained: bool


class RouteDecision(BaseModel):
    """The route chosen for a query, plus confidence and fallback flag."""

    route: str
    confidence: float
    fallback: bool


class RoutedResult(BaseModel):
    """Result of a routed query: the decision plus the underlying results."""

    decision: RouteDecision
    results: list[dict[str, object]]


class SearchResult(BaseModel):
    """A single search hit."""

    chunk_id: UUID
    text: str
    score: float
    source: str
    doc_id: UUID
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    chunk_index: int | None = None
    heading_path: list[str] | None = None
    # Issue #271: the textbook strategy's chunk-metadata keys, exposed to
    # search consumers. The strategy writes ``citation`` (a plain-English
    # page citation) and a sorted ``units`` list; both stay None for any
    # chunk without that metadata.
    citation: str | None = None
    units: list[str] | None = None


class DocumentResult(BaseModel):
    """Document metadata in list responses."""

    doc_id: UUID
    source: str
    source_type: str
    chunk_count: int
    created_at: str


class DocumentChunkResult(BaseModel):
    """One chunk of one document in the chunks-list window (Wave 5)."""

    chunk_id: UUID
    doc_id: UUID
    chunk_index: int
    text: str
    chunk_type: str | None = None
    start_line: int | None = None
    end_line: int | None = None


# The tail result models moved to ``domain/results.py`` (the 250-line
# rule); the re-exports keep every existing import path byte-identical.
