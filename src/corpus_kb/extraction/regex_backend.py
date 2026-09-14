"""Regex-based ontology extractor fallback."""

from __future__ import annotations

from .protocol import OntologyViolationError
from ..graph.extractor import extract_entities
from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation


_REGEX_TYPE_MAP = {
    "CONCEPT": "Concept",
    "CLASS": "Class",
    "FUNCTION": "Function",
}


class RegexExtractor:
    """Extractor wrapping the legacy regex entity extraction logic."""

    extractor_id: str = "regex"

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract entities from chunks using regex patterns.

        Entities are mapped to ontology types where possible. Relations are
        not derived by this backend.
        """
        entities: list[Entity] = []
        for chunk in chunks:
            extracted = extract_entities(
                chunk.text,
                source_type=chunk.source_type,
                source_document_id=source_document_id,
            )
            for entity in extracted:
                entity_type = _map_entity_type(entity.entity_type, ontology)
                entities.append(
                    Entity(
                        name=entity.name,
                        entity_type=entity_type,
                        source_type=chunk.source_type,
                        source_document_id=source_document_id,
                        chunk_id=chunk.chunk_id,
                        confidence=None,
                        extractor_id=self.extractor_id,
                        metadata=entity.metadata,
                    )
                )
        return entities, []


def _map_entity_type(raw_type: str, ontology: Ontology) -> str:
    """Map a regex entity type to an ontology type.

    Known regex types are normalized to title case. Unknown types fall back
    to ``Concept`` if it exists in the ontology, otherwise the first entity
    type. Raises ``OntologyViolationError`` when no mapping is possible.
    """
    normalized = _REGEX_TYPE_MAP.get(raw_type.upper())
    if normalized and normalized in ontology.entity_types:
        return normalized

    if raw_type in ontology.entity_types:
        return raw_type

    if "Concept" in ontology.entity_types:
        return "Concept"

    if ontology.entity_types:
        return ontology.entity_types[0]

    raise OntologyViolationError(
        kind="entity_type", value=raw_type, allowed=ontology.entity_types
    )
