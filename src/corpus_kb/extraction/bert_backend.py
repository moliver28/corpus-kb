"""Minimal spaCy NER extraction backend.

Uses spaCy en_core_web_sm (small model, ~12MB, CPU-only) for named entity
recognition. Falls back to regex backend on model load failure.

For advanced NER (BERT/transformers, BERTScore similarity, cross-document
coreference), see GitHub issue: Upgrade NER extraction to BERT/transformer models.
"""

from __future__ import annotations

import logging
from typing import Optional

from .protocol import Extractor
from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation

logger = logging.getLogger(__name__)

_SPACY_TO_ONTOLOGY = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "place",
    "LOC": "place",
    "PRODUCT": "concept",
    "EVENT": "concept",
    "WORK_OF_ART": "concept",
    "LANGUAGE": "concept",
    "DATE": "concept",
    "TIME": "concept",
    "MONEY": "concept",
    "QUANTITY": "concept",
    "PERCENT": "concept",
}


class BertExtractor(Extractor):
    """Minimal spaCy NER extractor using en_core_web_sm."""

    extractor_id = "bert"

    def __init__(self, ontology: Optional[Ontology] = None) -> None:
        self._ontology = ontology
        self._nlp = None
        self._fallback = None

        try:
            import spacy

            self._spacy = spacy
            try:
                self._nlp = spacy.load("en_core_web_sm")
                logger.info("spaCy en_core_web_sm loaded")
            except OSError:
                logger.warning(
                    "spaCy model not found. Run: python -m spacy download en_core_web_sm"
                )
                self._nlp = None
        except ImportError:
            logger.warning("spaCy not installed. Run: pip install spacy")
            self._spacy = None
            self._nlp = None

        if self._nlp is None:
            from .regex_backend import RegexExtractor

            self._fallback = RegexExtractor(ontology)
            logger.info("Using regex fallback for NER")

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: Optional[str] = None,
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract entities using spaCy NER. Falls back to regex."""
        if self._nlp is None:
            if self._fallback is not None:
                return self._fallback.extract(chunks, ontology, source_document_id)
            return [], []

        entities: list[Entity] = []
        relations: list[Relation] = []
        seen_names: set[str] = set()

        for chunk in chunks:
            if not chunk.text or not chunk.text.strip():
                continue

            doc = self._nlp(chunk.text[:10000])

            for ent in doc.ents:
                name = ent.text.strip()
                if not name or name in seen_names:
                    continue

                spacy_label = ent.label_
                entity_type = _SPACY_TO_ONTOLOGY.get(spacy_label, "concept")

                if ontology and entity_type not in ontology.entity_types:
                    entity_type = "concept"

                entity = Entity(
                    name=name,
                    entity_type=entity_type,
                    metadata={
                        "spacy_label": spacy_label,
                        "source_chunk_id": chunk.chunk_id,
                        "start_char": ent.start_char,
                        "end_char": ent.end_char,
                    },
                )
                entities.append(entity)
                seen_names.add(name)

        logger.info("spaCy NER: %d entities from %d chunks", len(entities), len(chunks))
        return entities, relations
