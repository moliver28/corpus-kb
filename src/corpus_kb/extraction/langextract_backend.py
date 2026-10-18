"""LangExtract-backed ontology extractor with recorded fixtures."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, cast

from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation
from ._langextract_types import (
    Extraction,
    LangExtractModule,
    NormalizedExtraction,
    build_examples,
    build_prompt_description,
    import_langextract,
)
from .protocol import ExtractStats, OntologyViolationError, Timer


class LangExtractExtractor:
    """Extractor using LangExtract with optional recorded fixtures.

    ``langextract`` is imported lazily so a bare install can still import
    this module. Recorded fixtures are keyed by ``sha256(chunk.text)`` so
    CI runs deterministically without live LLM calls.
    """

    extractor_id: str = "langextract"

    def __init__(
        self,
        fixture_dir: str | Path | None = None,
        live_fallback: bool = True,
    ) -> None:
        """Initialize the extractor.

        Args:
            fixture_dir: Directory containing recorded ``<sha256>.jsonl``
                fixtures. When provided, fixtures are preferred over live
                extraction for matching inputs.
            live_fallback: If ``True`` and no fixture matches a chunk, call
                the live ``langextract`` API. If ``False``, a missing fixture
                raises ``FileNotFoundError``.
        """
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.live_fallback = live_fallback
        self._lx: LangExtractModule | None = None

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[Relation], ExtractStats]:
        """Extract entities and derive relations from chunks.

        Counts are honest: every entity candidate that fails offset
        validation and every relation candidate dropped for unresolved
        endpoints or the per-chunk cap is tallied in the returned stats.
        """
        timer = Timer(self.extractor_id)
        entities: list[Entity] = []
        relations: list[Relation] = []
        requested_entities = 0
        dropped_entities_other = 0
        requested_relations = 0
        dropped_relations_by_cap = 0
        dropped_relations_other = 0

        for chunk in chunks:
            chunk_entities, chunk_relation_extractions, ent_requested, ent_dropped = (
                self._extract_chunk_entities(chunk, ontology, source_document_id)
            )
            requested_entities += ent_requested
            dropped_entities_other += ent_dropped
            entities.extend(chunk_entities)
            requested_relations += len(chunk_relation_extractions)
            chunk_relations, by_cap, by_other = _parse_typed_relations(
                chunk_relation_extractions,
                chunk_entities,
                chunk,
                ontology,
                self.extractor_id,
            )
            relations.extend(chunk_relations)
            dropped_relations_by_cap += by_cap
            dropped_relations_other += by_other

        kept_entities = len(entities)
        kept_relations = len(relations)
        stats = ExtractStats(
            backend=self.extractor_id,
            requested_entities=requested_entities,
            kept_entities=kept_entities,
            requested_relations=requested_relations,
            kept_relations=kept_relations,
            dropped_by_cap=dropped_relations_by_cap,
            dropped_by_other=dropped_entities_other + dropped_relations_other,
            duration_ms=timer.elapsed_ms(),
        )
        return entities, relations, stats

    def _extract_chunk_entities(
        self,
        chunk: Chunk,
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[NormalizedExtraction], int, int]:
        """Extract one chunk.

        Returns ``(entities, relation_extractions, requested, dropped)``
        where ``requested`` counts entity-typed candidates before offset
        validation and ``dropped`` counts the ones rejected for invalid
        offsets.
        """
        text = chunk.text
        if not text:
            return [], [], 0, 0

        extractions = self._load_extractions(text, ontology)
        entities: list[Entity] = []
        relation_extractions: list[NormalizedExtraction] = []
        requested = 0
        dropped = 0
        for extraction in extractions:
            if extraction.extraction_class in ontology.relation_types:
                relation_extractions.append(extraction)
                continue

            if extraction.extraction_class not in ontology.entity_types:
                raise OntologyViolationError(
                    kind="entity_type",
                    value=extraction.extraction_class,
                    allowed=ontology.entity_types,
                )

            requested += 1
            start = extraction.start_pos
            end = extraction.end_pos
            if start is None or end is None:
                dropped += 1
                continue

            text_length = len(text)
            if start < 0 or end > text_length or start >= end:
                dropped += 1
                logging.warning(
                    "Skipping invalid extraction offsets for '%s': "
                    "start=%d, end=%d, text_length=%d",
                    extraction.extraction_text,
                    start,
                    end,
                    text_length,
                )
                continue

            metadata: dict[str, Any] = {"extractor": self.extractor_id, "text": text}
            if extraction.confidence is not None:
                metadata["confidence"] = extraction.confidence
            entities.append(
                Entity(
                    name=extraction.extraction_text,
                    entity_type=extraction.extraction_class,
                    source_type=chunk.source_type,
                    source_document_id=source_document_id,
                    chunk_id=chunk.chunk_id,
                    source_start_char=start,
                    source_end_char=end,
                    confidence=extraction.confidence,
                    extractor_id=self.extractor_id,
                    metadata=metadata,
                )
            )
        return entities, relation_extractions, requested, dropped

    def _load_extractions(self, text: str, ontology: Ontology) -> list[NormalizedExtraction]:
        if self.fixture_dir:
            key = _sha256(text)
            fixture_path = self.fixture_dir / f"{key}.jsonl"
            if fixture_path.exists():
                return _load_fixture(fixture_path)
            if not self.live_fallback:
                raise FileNotFoundError(f"No LangExtract fixture for text hash {key}")

        if self._lx is None:
            self._lx = import_langextract()
        result = self._lx.extract(
            text_or_documents=text,
            prompt_description=build_prompt_description(ontology),
            examples=build_examples(self._lx, ontology),
        )
        docs = result if isinstance(result, list) else [result]
        return [_normalize_extraction(extraction) for doc in docs for extraction in doc.extractions]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_fixture(path: Path) -> list[NormalizedExtraction]:
    extractions: list[NormalizedExtraction] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = cast(dict[str, Any], json.loads(line))

            cls = raw.get("extraction_class")
            extraction_text = raw.get("extraction_text")
            if not isinstance(cls, str) or not isinstance(extraction_text, str):
                continue

            interval_raw = raw.get("char_interval")
            start: int | None = None
            end: int | None = None
            if isinstance(interval_raw, dict):
                interval = cast(dict[str, Any], interval_raw)
                start_raw = interval.get("start_pos")
                end_raw = interval.get("end_pos")
                if isinstance(start_raw, int) and not isinstance(start_raw, bool):
                    start = start_raw
                if isinstance(end_raw, int) and not isinstance(end_raw, bool):
                    end = end_raw

            conf_raw = raw.get("confidence")
            confidence = (
                float(conf_raw)
                if isinstance(conf_raw, (int, float)) and not isinstance(conf_raw, bool)
                else None
            )

            attrs_raw = raw.get("attributes")
            attributes = attrs_raw if isinstance(attrs_raw, dict) else None

            extractions.append(
                NormalizedExtraction(
                    extraction_class=cls,
                    extraction_text=extraction_text,
                    start_pos=start,
                    end_pos=end,
                    confidence=confidence,
                    attributes=attributes,
                )
            )
    return extractions


def _normalize_extraction(extraction: Extraction) -> NormalizedExtraction:
    interval = extraction.char_interval
    attributes = getattr(extraction, "attributes", None)
    return NormalizedExtraction(
        extraction_class=extraction.extraction_class,
        extraction_text=extraction.extraction_text,
        start_pos=interval.start_pos if interval else None,
        end_pos=interval.end_pos if interval else None,
        confidence=None,
        attributes=attributes if isinstance(attributes, dict) else None,
    )


MAX_RELATIONS_PER_CHUNK = 10


def _parse_typed_relations(
    relation_extractions: list[NormalizedExtraction],
    entities: list[Entity],
    chunk: Chunk,
    ontology: Ontology,
    extractor_id: str,
) -> tuple[list[Relation], int, int]:
    """Parse relation triples, reporting where candidates were dropped.

    Returns ``(relations, dropped_by_cap, dropped_by_other)``.
    ``dropped_by_cap`` counts candidates past the per-chunk cap;
    ``dropped_by_other`` counts candidates whose subject/object never
    resolved to an extracted entity.
    """
    by_name = {e.name: e for e in entities}
    relations: list[Relation] = []
    dropped_by_cap = 0
    dropped_by_other = 0
    for extraction in relation_extractions:
        if extraction.extraction_class not in ontology.relation_types:
            raise OntologyViolationError(
                kind="relation_type",
                value=extraction.extraction_class,
                allowed=ontology.relation_types,
            )
        attrs = extraction.attributes or {}
        subject = by_name.get(str(attrs.get("subject", "")))
        obj = by_name.get(str(attrs.get("object", "")))
        if subject is None or obj is None:
            dropped_by_other += 1
            logging.warning(
                "Dropping unresolved relation triple '%s' in chunk %s: "
                "subject/object not found among extracted entities.",
                extraction.extraction_class,
                chunk.chunk_id,
            )
            continue
        if len(relations) >= MAX_RELATIONS_PER_CHUNK:
            dropped_by_cap += 1
            continue
        relations.append(
            Relation(
                source_entity_id=subject.entity_id,
                target_entity_id=obj.entity_id,
                relation_type=extraction.extraction_class,
                chunk_id=chunk.chunk_id,
                confidence=cast("float | None", attrs.get("confidence")),
                extractor_id=extractor_id,
                metadata={},
            )
        )
    return relations, dropped_by_cap, dropped_by_other
