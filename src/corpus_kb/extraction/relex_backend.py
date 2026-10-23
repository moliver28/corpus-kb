"""Zero-shot relation extraction with GLiNER-Relex (``gliner`` package).

The extractor implements the :class:`Extractor` protocol. It never runs
NER itself when entity spans are supplied: ``MLExtractor`` runs GLiNER2
first and passes the produced entities in, so every relation endpoint
resolves to a real entity. Standalone use emits thin entities from the
triplet spans (recorded honestly in ``stats.notes``) so relations always
resolve — the store's entity dedup merges them with later passes.

Relation labels are the ontology's natural-language descriptions (or the
type name with underscores unwrapped), paired with every configured
mention type as head label. The pair list is capped by
``max_label_pairs``; the cap is recorded as a stats note.
"""

from __future__ import annotations

import logging
from typing import Any

from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation
from ._gliner_types import RelexModel, import_gliner
from .gliner_backend import TYPE_DEFINITIONS, _map_mention_type
from .protocol import ExtractStats, OntologyViolationError, Timer

logger = logging.getLogger(__name__)

DEFAULT_RELEX_MODEL = "knowledgator/gliner-relex-large-v1.0"
MIN_RELATION_CONFIDENCE = 0.5
MIN_SPAN_OVERLAP = 0.5


class RelexExtractor:
    """GLiNER-Relex relation extraction over ontology relation types."""

    extractor_id: str = "relex"

    def __init__(
        self,
        model: str = DEFAULT_RELEX_MODEL,
        types: list[str] | None = None,
        threshold: float = MIN_RELATION_CONFIDENCE,
        max_label_pairs: int = 40,
    ) -> None:
        selected = types if types else list(TYPE_DEFINITIONS)
        unknown = [t for t in selected if t not in TYPE_DEFINITIONS]
        if unknown:
            raise ValueError(
                f"Unknown relex mention types: {unknown}; supported: {sorted(TYPE_DEFINITIONS)}"
            )
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"relex threshold must be in (0, 1], got {threshold}")
        if max_label_pairs < 1:
            raise ValueError(f"relex max_label_pairs must be >= 1, got {max_label_pairs}")
        self._model_id = model
        self._types = selected
        self._threshold = threshold
        self._max_label_pairs = max_label_pairs
        self._model: RelexModel | None = None

    def _load_model(self) -> RelexModel:
        if self._model is None:
            module = import_gliner()
            logger.info("Loading GLiNER-Relex model %s", self._model_id)
            self._model = module.GLiNER.from_pretrained(self._model_id)
        return self._model

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
        entities: list[Entity] | None = None,
    ) -> tuple[list[Entity], list[Relation], ExtractStats]:
        """Extract relations; link endpoints to ``entities`` when supplied."""
        timer = Timer(self.extractor_id)
        model = self._load_model()
        pairs, capped_note = self._label_pairs(ontology)
        known = [e for e in (entities or []) if e.chunk_id]
        by_chunk: dict[str, list[Entity]] = {}
        for entity in known:
            by_chunk.setdefault(entity.chunk_id or "", []).append(entity)

        relations: list[Relation] = []
        emitted_entities: list[Entity] = []
        pseudo_entities = 0
        requested = 0
        dropped_by_confidence = 0
        dropped_by_other = 0
        for chunk in chunks:
            text = chunk.text
            if not text or not text.strip():
                continue
            for triplet in _predict(model, text, pairs, self._types, self._threshold):
                requested += 1
                score = _as_float(_field(triplet, "score"))
                if score < self._threshold:
                    dropped_by_confidence += 1
                    continue
                head = _span(_field(triplet, "head"))
                tail = _span(_field(triplet, "tail"))
                if head is None or tail is None:
                    dropped_by_other += 1
                    continue
                rel_type = str(_field(triplet, "label")).upper().replace(" ", "_")
                if rel_type not in ontology.relation_types:
                    dropped_by_other += 1
                    continue
                chunk_entities = by_chunk.get(chunk.chunk_id or "", [])
                source = _link(head, chunk_entities)
                target = _link(tail, chunk_entities)
                if source is None:
                    source = _pseudo_entity(head, chunk, ontology, source_document_id, score)
                    emitted_entities.append(source)
                    pseudo_entities += 1
                if target is None:
                    target = _pseudo_entity(tail, chunk, ontology, source_document_id, score)
                    emitted_entities.append(target)
                    pseudo_entities += 1
                relations.append(
                    Relation(
                        source_entity_id=source.entity_id,
                        target_entity_id=target.entity_id,
                        relation_type=rel_type,
                        weight=score,
                        chunk_id=chunk.chunk_id,
                        source_start_char=head[0],
                        source_end_char=head[1],
                        confidence=score,
                        extractor_id=self.extractor_id,
                        metadata={
                            "extractor": self.extractor_id,
                            "model": self._model_id,
                            "confidence": score,
                        },
                    )
                )
        notes = [f"relex standalone pass emitted {pseudo_entities} span entities"]
        if capped_note:
            notes.append(capped_note)
        stats = ExtractStats(
            backend=self.extractor_id,
            requested_relations=requested,
            kept_relations=len(relations),
            dropped_by_confidence=dropped_by_confidence,
            dropped_by_other=dropped_by_other,
            duration_ms=timer.elapsed_ms(),
            notes=tuple(notes),
        )
        return emitted_entities, relations, stats

    def _label_pairs(self, ontology: Ontology) -> tuple[list[tuple[str, str]], str]:
        """(mention type, natural-language relation) pairs plus a cap note."""
        natural: dict[str, str] = {}
        for rel_type in ontology.relation_types:
            text = ontology.relation_descriptions.get(rel_type, "")
            natural[rel_type] = (
                text.strip() if text and text.strip() else rel_type.lower().replace("_", " ")
            )
        pairs = [
            (mention, natural[rel_type])
            for rel_type in ontology.relation_types
            for mention in self._types
        ]
        note = ""
        if len(pairs) > self._max_label_pairs:
            note = f"relex label pairs capped at {self._max_label_pairs} of {len(pairs)}"
            pairs = pairs[: self._max_label_pairs]
        return pairs, note


def _predict(
    model: RelexModel,
    text: str,
    pairs: list[tuple[str, str]],
    types: list[str],
    threshold: float,
) -> list[Any]:
    raw = model.predict_relations(text, labels=pairs, entities=types, threshold=threshold)
    return list(raw) if raw else []


def _field(triplet: Any, name: str) -> Any:
    if isinstance(triplet, dict):
        return triplet.get(name)
    return getattr(triplet, name, None)


def _span(value: Any) -> tuple[int, int, str, str] | None:
    """(start, end, text, label) from a mapping- or attribute-like span."""
    if value is None:
        return None
    if isinstance(value, dict):
        start, end = value.get("start"), value.get("end")
        text = value.get("text", "")
        label = value.get("label", "")
    else:
        start, end = getattr(value, "start", None), getattr(value, "end", None)
        text = getattr(value, "text", "")
        label = getattr(value, "label", "")
    if start is None or end is None:
        return None
    return int(start), int(end), str(text), str(label)


def _link(span: tuple[int, int, str, str], candidates: list[Entity]) -> Entity | None:
    """Best entity whose span overlaps >= MIN_SPAN_OVERLAP of the smaller."""
    best: tuple[float, Entity] | None = None
    for entity in candidates:
        if entity.source_start_char is None or entity.source_end_char is None:
            continue
        inter_start = max(span[0], entity.source_start_char)
        inter_end = min(span[1], entity.source_end_char)
        if inter_end <= inter_start:
            continue
        smaller = min(span[1] - span[0], entity.source_end_char - entity.source_start_char)
        overlap = (inter_end - inter_start) / smaller if smaller > 0 else 0.0
        if overlap >= MIN_SPAN_OVERLAP and (best is None or overlap > best[0]):
            best = (overlap, entity)
    return best[1] if best else None


def _pseudo_entity(
    span: tuple[int, int, str, str],
    chunk: Chunk,
    ontology: Ontology,
    source_document_id: str,
    score: float,
) -> Entity:
    """Thin entity from a triplet span so the relation endpoint resolves."""
    hint = span[3].lower() if span[3] else "person"
    try:
        entity_type = _map_mention_type(hint, ontology)
    except OntologyViolationError:
        entity_type = ontology.entity_types[0]
    return Entity(
        name=span[2],
        entity_type=entity_type,
        source_type=chunk.source_type,
        source_document_id=source_document_id,
        chunk_id=chunk.chunk_id,
        source_start_char=span[0],
        source_end_char=span[1],
        confidence=score,
        extractor_id="relex",
        metadata={"extractor": "relex", "pseudo": True, "confidence": score},
    )


def _as_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
