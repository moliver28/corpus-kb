"""LangExtract-backed ontology extractor with recorded fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from src.extraction._langextract_types import (
    Extraction,
    LangExtractModule,
    NormalizedExtraction,
    build_examples,
    build_prompt_description,
    import_langextract,
)
from src.extraction.protocol import OntologyViolationError
from src.ontology import Ontology
from src.utils.models import Chunk, Entity, Relation


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
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract entities and derive relations from chunks."""
        entities: list[Entity] = []
        relations: list[Relation] = []

        for chunk in chunks:
            chunk_entities = self._extract_chunk_entities(
                chunk, ontology, source_document_id
            )
            entities.extend(chunk_entities)
            relations.extend(_derive_relations(chunk_entities, chunk, ontology))

        return entities, relations

    def _extract_chunk_entities(
        self,
        chunk: Chunk,
        ontology: Ontology,
        source_document_id: str,
    ) -> list[Entity]:
        text = chunk.text
        if not text:
            return []

        extractions = self._load_extractions(text, ontology)
        entities: list[Entity] = []
        for extraction in extractions:
            if extraction.extraction_class not in ontology.entity_types:
                raise OntologyViolationError(
                    kind="entity_type",
                    value=extraction.extraction_class,
                    allowed=ontology.entity_types,
                )

            start = extraction.start_pos
            end = extraction.end_pos
            if start is None or end is None:
                continue

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
                    metadata={"text": text},
                )
            )
        return entities

    def _load_extractions(
        self, text: str, ontology: Ontology
    ) -> list[NormalizedExtraction]:
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
        if isinstance(result, list):
            docs = result
        else:
            docs = [result]
        return [
            _normalize_extraction(extraction)
            for doc in docs
            for extraction in doc.extractions
        ]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_fixture(path: Path) -> list[NormalizedExtraction]:
    extractions: list[NormalizedExtraction] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = cast(dict[str, object], json.loads(line))

            cls = raw.get("extraction_class")
            extraction_text = raw.get("extraction_text")
            if not isinstance(cls, str) or not isinstance(extraction_text, str):
                continue

            interval_raw = raw.get("char_interval")
            start: int | None = None
            end: int | None = None
            if isinstance(interval_raw, dict):
                interval = cast(dict[str, object], interval_raw)
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

            extractions.append(
                NormalizedExtraction(
                    extraction_class=cls,
                    extraction_text=extraction_text,
                    start_pos=start,
                    end_pos=end,
                    confidence=confidence,
                )
            )
    return extractions


def _normalize_extraction(extraction: Extraction) -> NormalizedExtraction:
    interval = extraction.char_interval
    return NormalizedExtraction(
        extraction_class=extraction.extraction_class,
        extraction_text=extraction.extraction_text,
        start_pos=interval.start_pos if interval else None,
        end_pos=interval.end_pos if interval else None,
        confidence=None,
    )


def _derive_relations(
    entities: list[Entity], chunk: Chunk, ontology: Ontology
) -> list[Relation]:
    relation_type = _pick_relation_type(ontology)
    relations: list[Relation] = []
    for idx, source in enumerate(entities):
        for target in entities[idx + 1 :]:
            confidence = _relation_confidence(source, target)
            relations.append(
                Relation(
                    source_entity_id=source.entity_id,
                    target_entity_id=target.entity_id,
                    relation_type=relation_type,
                    chunk_id=chunk.chunk_id,
                    confidence=confidence,
                    extractor_id=LangExtractExtractor.extractor_id,
                    metadata={},
                )
            )
    return relations


def _pick_relation_type(ontology: Ontology) -> str:
    for candidate in ("MENTIONS", "RELATED_TO"):
        if candidate in ontology.relation_types:
            return candidate
    if ontology.relation_types:
        return ontology.relation_types[0]
    raise OntologyViolationError(
        kind="relation_type", value="", allowed=ontology.relation_types
    )


def _relation_confidence(source: Entity, target: Entity) -> float | None:
    if source.confidence is not None and target.confidence is not None:
        return (source.confidence + target.confidence) / 2.0
    return source.confidence if source.confidence is not None else target.confidence
