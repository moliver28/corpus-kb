"""Pure span helpers for the GLiNER-Relex backend (split past the size gate).

Nothing here touches the model or the database: these functions bridge
gliner relation payloads into the backend's triplet shape, read spans
defensively, and build the thin pseudo-entities that keep every relation
endpoint resolved. ``relex_backend`` owns the extractor, the model load,
and the extraction loop.
"""

from __future__ import annotations

from typing import Any

from ..ontology import Ontology
from ..utils.models import Chunk, Entity
from .gliner_backend import _map_mention_type
from .protocol import OntologyViolationError

#: Endpoints link to the best entity overlapping at least this fraction
#: of the smaller span.
MIN_SPAN_OVERLAP = 0.5


def field(triplet: Any, name: str) -> Any:
    """Read one field from a mapping- or attribute-like payload."""
    if isinstance(triplet, dict):
        return triplet.get(name)
    return getattr(triplet, name, None)


def as_float(value: Any) -> float:
    """Coerce a JSON-ish number to float; anything else is 0.0."""
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def bridge_triplet(relation: Any) -> dict[str, Any]:
    """One gliner relation dict -> the backend's triplet dict shape."""
    mapped: dict[str, Any] = {}
    for side in ("head", "tail"):
        span = side_dict(field(relation, side))
        mapped[side] = {
            "start": span.get("start"),
            "end": span.get("end"),
            "text": str(span.get("text", "")),
            "label": str(span.get("type", span.get("label", ""))),
        }
    mapped["label"] = str(field(relation, "relation") or field(relation, "label") or "")
    mapped["score"] = as_float(field(relation, "score"))
    return mapped


def side_dict(value: Any) -> dict[str, Any]:
    """A head/tail payload as a dict, from mapping- or attribute-shaped."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {
        key: getattr(value, key)
        for key in ("start", "end", "text", "type", "label")
        if hasattr(value, key)
    }


def span(value: Any) -> tuple[int, int, str, str] | None:
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


def link(span: tuple[int, int, str, str], candidates: list[Entity]) -> Entity | None:
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


def pseudo_entity(
    span: tuple[int, int, str, str],
    chunk: Chunk,
    ontology: Ontology,
    source_document_id: str,
    score: float,
) -> Entity:
    """Thin entity from a triplet span so the relation endpoint resolves.

    The span's mention-type hint stays in provenance (``gliner_type``):
    spans whose ontology type degrades to Concept stay scorable.
    """
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
        metadata={
            "extractor": "relex",
            "pseudo": True,
            "confidence": score,
            "gliner_type": hint,
        },
    )
