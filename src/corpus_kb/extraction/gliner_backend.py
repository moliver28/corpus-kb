"""Opt-in GLiNER2 schema-conditioned NER extractor.

Runs ``fastino/gliner2-large-v1`` locally (HF revision pinned from
config) over SIX mention-level types, each passed to the model with a
one-line definition at inference time:

    person, organization, location, date, money, product

Mention spans are mapped onto ontology entity types (person -> Person,
organization -> Org, product -> Product, the rest fall back to Concept)
and emitted with per-span confidence. GLiNER2 does NOT extract
relations: this backend returns an empty relation list and records that
limitation in ``ExtractStats.notes`` — never silently.

Provenance: every entity carries ``metadata`` with ``extractor``,
``model``, ``revision`` and ``confidence`` so the Postgres write path
persists provenance even though the entities table has no dedicated
columns.

The ``gliner2`` package is optional (``pip install .[ner]``) and never
installed in CI; all imports are lazy (see ``_gliner2_types.py``).
"""

from __future__ import annotations

import logging
from typing import cast

from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation
from ._gliner2_types import GlinerModel, GlinerSpan, import_gliner2
from .protocol import ExtractStats, OntologyViolationError, Timer

logger = logging.getLogger(__name__)

DEFAULT_GLINER_MODEL = "fastino/gliner2-large-v1"
DEFAULT_GLINER_REVISION = "main"

#: Spans scoring below this are dropped and counted in
#: ``dropped_by_confidence``. Kept as a code constant (not a config key)
#: so the eval promotion gate measures a fixed operating point.
MIN_SPAN_CONFIDENCE = 0.35

#: One-line type definitions passed to the model at inference time —
#: schema conditioning, not just bare label strings.
TYPE_DEFINITIONS: dict[str, str] = {
    "person": "Named individual people (fictional or real).",
    "organization": "Companies, agencies, institutions, teams, and other named organizations.",
    "location": "Named geographic places: cities, countries, addresses, regions, venues.",
    "date": "Explicit dates, years, and temporal expressions anchored to a calendar.",
    "money": "Monetary amounts, including currency symbols or codes.",
    "product": "Named products, hardware, software systems, and branded goods.",
}

#: gliner mention type -> candidate ontology entity types, first match wins.
_MENTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "person": ("Person",),
    "organization": ("Org", "Organization"),
    "location": ("Place", "Location"),
    "date": ("Date",),
    "money": ("Money", "Metric"),
    "product": ("Product",),
}


class GlinerExtractor:
    """GLiNER2 mention-level NER with schema conditioning.

    Args:
        model: HuggingFace model id (default ``fastino/gliner2-large-v1``).
        revision: HuggingFace revision to pin (default ``main``).
        types: Mention types to extract. Must be a non-empty subset of
            :data:`TYPE_DEFINITIONS`; defaults to all six.
    """

    extractor_id: str = "gliner"

    def __init__(
        self,
        model: str = DEFAULT_GLINER_MODEL,
        revision: str = DEFAULT_GLINER_REVISION,
        types: list[str] | None = None,
    ) -> None:
        selected = types if types else list(TYPE_DEFINITIONS)
        unknown = [t for t in selected if t not in TYPE_DEFINITIONS]
        if unknown:
            raise ValueError(
                f"Unknown gliner mention types: {unknown}; supported: {sorted(TYPE_DEFINITIONS)}"
            )
        self._model_id = model
        self._revision = revision
        self._types = selected
        self._schema: dict[str, str] = {t: TYPE_DEFINITIONS[t] for t in selected}
        self._model: GlinerModel | None = None

    def _load_model(self) -> GlinerModel:
        """Lazily import gliner2 and load the pinned HF model (once)."""
        if self._model is None:
            module = import_gliner2()
            logger.info("Loading GLiNER2 model %s (revision %s)", self._model_id, self._revision)
            self._model = module.GLiNER2.from_pretrained(self._model_id, revision=self._revision)
        return self._model

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[Relation], ExtractStats]:
        """Extract mention-level entities. Never produces relations."""
        timer = Timer(self.extractor_id)
        model = self._load_model()

        entities: list[Entity] = []
        requested = 0
        dropped_by_confidence = 0
        dropped_by_other = 0
        for chunk in chunks:
            text = chunk.text
            if not text or not text.strip():
                continue
            for span in _predict(model, text, self._schema):
                requested += 1
                confidence = span.score
                if confidence < MIN_SPAN_CONFIDENCE:
                    dropped_by_confidence += 1
                    continue
                start, end = int(span.start), int(span.end)
                if start < 0 or end > len(text) or start >= end:
                    dropped_by_other += 1
                    logger.warning(
                        "Skipping out-of-range gliner span %r [%d,%d) for chunk %s",
                        span.text,
                        start,
                        end,
                        chunk.chunk_id,
                    )
                    continue
                mention_type = span.label.lower()
                metadata: dict[str, object] = {
                    "extractor": self.extractor_id,
                    "model": self._model_id,
                    "revision": self._revision,
                    "confidence": confidence,
                    "gliner_type": mention_type,
                }
                entities.append(
                    Entity(
                        name=span.text,
                        entity_type=_map_mention_type(mention_type, ontology),
                        source_type=chunk.source_type,
                        source_document_id=source_document_id,
                        chunk_id=chunk.chunk_id,
                        source_start_char=start,
                        source_end_char=end,
                        confidence=confidence,
                        extractor_id=self.extractor_id,
                        metadata=metadata,
                    )
                )

        kept = len(entities)
        stats = ExtractStats(
            backend=self.extractor_id,
            requested_entities=requested,
            kept_entities=kept,
            dropped_by_confidence=dropped_by_confidence,
            dropped_by_other=dropped_by_other,
            duration_ms=timer.elapsed_ms(),
            notes=(
                "gliner2 does not extract relations; run a relation-capable "
                "backend when relations are needed",
            ),
        )
        return entities, [], stats


def _predict(
    model: GlinerModel,
    text: str,
    schema: dict[str, str],
) -> list[GlinerSpan]:
    """Call extract_entities and normalize spans defensively.

    gliner2 span payloads have been observed as both plain dicts
    (``{"text", "start", "end", "score", "label"}``) and small objects
    with those attributes; both are handled here.
    """
    raw = model.extract_entities(text, schema)
    spans: list[GlinerSpan] = []
    for bucket in raw.values():
        for item in bucket:
            if isinstance(item, dict):
                spans.append(_DictSpan(cast(dict[str, object], item)))
            else:
                spans.append(cast(GlinerSpan, item))
    return spans


class _DictSpan:
    """Attribute-view over a dict-shaped gliner span payload."""

    __slots__ = ("end", "label", "score", "start", "text")

    def __init__(self, data: dict[str, object]) -> None:
        self.text = str(data.get("text", ""))
        self.start = _as_int(data.get("start"))
        self.end = _as_int(data.get("end"))
        self.score = _as_float(data.get("score"))
        self.label = str(data.get("label", data.get("entity_group", "")))


def _as_int(value: object) -> int:
    """Coerce a JSON-ish object to int, 0 when not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _as_float(value: object) -> float:
    """Coerce a JSON-ish object to float, 0.0 when not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _map_mention_type(mention_type: str, ontology: Ontology) -> str:
    """Map a gliner mention type to an ontology entity type.

    Synonym table first (gliner says ``organization``, the ontology says
    ``Org``), then a capitalized direct hit, then the Concept fallback.
    """
    for candidate in _MENTION_SYNONYMS.get(mention_type, (mention_type.capitalize(),)):
        if candidate in ontology.entity_types:
            return candidate

    if "Concept" in ontology.entity_types:
        return "Concept"

    if ontology.entity_types:
        return ontology.entity_types[0]

    raise OntologyViolationError(
        kind="entity_type", value=mention_type, allowed=ontology.entity_types
    )
