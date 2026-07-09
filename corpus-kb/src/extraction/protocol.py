"""Pluggable ontology extractor protocol and shared exceptions."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation


class OntologyViolationError(Exception):
    """Raised when an entity or relation type is not declared in the ontology."""

    def __init__(self, kind: str, value: str, allowed: list[str]) -> None:
        message = f"Ontology violation: {kind} '{value}' not in {allowed}"
        super().__init__(message)
        self.kind = kind
        self.value = value
        self.allowed = allowed


@runtime_checkable
class Extractor(Protocol):
    """Protocol for pluggable ontology-driven extractors."""

    extractor_id: str

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract entities and typed relations from chunks.

        Args:
            chunks: Text chunks to process.
            ontology: Ontology constraining allowed entity/relation types.
            source_document_id: Document these chunks belong to.

        Returns:
            Tuple of (entities, relations).

        Raises:
            OntologyViolationError: If any emitted type is not in the ontology.
        """
        raise NotImplementedError
