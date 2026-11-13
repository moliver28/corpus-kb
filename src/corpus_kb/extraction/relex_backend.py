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
from .gliner_backend import TYPE_DEFINITIONS
from .protocol import ExtractStats, Timer
from .relex_spans import (
    as_float,
    bridge_triplet,
    field,
    link,
    pseudo_entity,
    span,
)

logger = logging.getLogger(__name__)

DEFAULT_RELEX_MODEL = "knowledgator/gliner-relex-large-v1.0"
MIN_RELATION_CONFIDENCE = 0.5


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
        pairs, capped_note, label_map = self._label_pairs(ontology)
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
            for triplet in _predict(model, text, pairs, self._threshold):
                requested += 1
                score = as_float(field(triplet, "score"))
                if score < self._threshold:
                    dropped_by_confidence += 1
                    continue
                head = span(field(triplet, "head"))
                tail = span(field(triplet, "tail"))
                if head is None or tail is None:
                    dropped_by_other += 1
                    continue
                raw_label = str(field(triplet, "label")).upper().replace(" ", "_")
                rel_type = label_map.get(raw_label, raw_label)
                if rel_type not in ontology.relation_types:
                    dropped_by_other += 1
                    continue
                chunk_entities = by_chunk.get(chunk.chunk_id or "", [])
                source = link(head, chunk_entities)
                target = link(tail, chunk_entities)
                if source is None:
                    source = pseudo_entity(head, chunk, ontology, source_document_id, score)
                    emitted_entities.append(source)
                    pseudo_entities += 1
                if target is None:
                    target = pseudo_entity(tail, chunk, ontology, source_document_id, score)
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

    def _label_pairs(self, ontology: Ontology) -> tuple[list[tuple[str, str]], str, dict[str, str]]:
        """(mention type, natural-language relation) pairs plus a cap note.

        The third value maps the NORMALIZED natural label back to the
        ontology type: the model echoes the label text it was given, so
        descriptions must translate home before the relation_types
        membership check.
        """
        natural: dict[str, str] = {}
        for rel_type in ontology.relation_types:
            text = ontology.relation_descriptions.get(rel_type, "")
            natural[rel_type] = (
                text.strip() if text and text.strip() else rel_type.lower().replace("_", " ")
            )
        label_map = {text.upper().replace(" ", "_"): rel_type for rel_type, text in natural.items()}
        pairs = [
            (mention, natural[rel_type])
            for rel_type in ontology.relation_types
            for mention in self._types
        ]
        note = ""
        if len(pairs) > self._max_label_pairs:
            note = f"relex label pairs capped at {self._max_label_pairs} of {len(pairs)}"
            pairs = pairs[: self._max_label_pairs]
        return pairs, note, label_map


def _predict(
    model: RelexModel,
    text: str,
    pairs: list[tuple[str, str]],
    threshold: float,
) -> list[dict[str, Any]]:
    """One relation pass over the verified gliner 0.2.29 API.

    gliner 0.2.29 returns ``(entities, relations)`` where each relation
    carries head/tail span dicts keyed ``type`` and the relation name
    under ``relation``. The extraction loop consumes the backend's own
    triplet shape, so this bridge maps keys here instead of at every
    consumer.
    """
    labels = {mention: TYPE_DEFINITIONS.get(mention, mention) for mention, _natural in pairs}
    relation_names = list(dict.fromkeys(natural for _mention, natural in pairs))
    _entities, raw = model.predict_relations(
        text,
        labels,
        relation_names,
        threshold=threshold,
        relation_threshold=threshold,
    )
    return [bridge_triplet(item) for item in (raw or [])]
