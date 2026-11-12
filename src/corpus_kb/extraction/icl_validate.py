"""Validation for synthesized few-shot exemplar batches.

A batch is usable only when every example is well-formed and the set
covers each declared relation type exactly once. ``exemplar_defects``
lists every reason a batch must be rejected; an empty list means the
batch may go into the prompt. The langextract backend falls back to the
static example set when the list is not empty.
"""

from __future__ import annotations

from typing import cast

from ..ontology import Ontology


def exemplar_defects(batch: tuple[dict[str, object], ...], ontology: Ontology) -> list[str]:
    """Every reason the batch is not a usable few-shot set; empty = valid."""
    defects: list[str] = []
    if not batch:
        return ["batch is empty"]
    seen_texts: set[str] = set()
    relation_seen: dict[str, int] = {}
    for index, exemplar in enumerate(batch):
        text = exemplar.get("text")
        raw = exemplar.get("extractions")
        if not isinstance(text, str) or not text:
            defects.append(f"example {index} has no text")
            continue
        if text in seen_texts:
            defects.append(f"example {index} duplicates an example text")
        seen_texts.add(text)
        if not isinstance(raw, list) or not raw:
            defects.append(f"example {index} has no extractions")
            continue
        items: list[dict[str, object]] = [i for i in raw if isinstance(i, dict)]
        if len(items) != len(raw):
            defects.append(f"example {index} has a non-mapping extraction")
        relation_count = sum(
            1
            for item in items
            if isinstance(item.get("extraction_class"), str)
            and cast(str, item.get("extraction_class")) in ontology.relation_types
        )
        if relation_count != 1:
            defects.append(f"example {index} has {relation_count} relation extractions")
        _defects_for_example(index, text, items, ontology, relation_seen, defects)
    wrong_coverage = [
        relation for relation in ontology.relation_types if relation_seen.get(relation, 0) != 1
    ]
    if wrong_coverage:
        defects.append(f"relation coverage wrong for {wrong_coverage}")
    return defects


def _defects_for_example(
    index: int,
    text: str,
    items: list[dict[str, object]],
    ontology: Ontology,
    relation_seen: dict[str, int],
    defects: list[str],
) -> None:
    # Same-type relation pairs repeat one mention text, so spans are
    # tracked per mention as an occurrence list, not a single interval.
    # Entities register first; relations are checked second, so item
    # order inside one example does not matter.
    occurrences: dict[str, list[tuple[int, int]]] = {}
    for item in items:
        cls = item.get("extraction_class")
        mention = item.get("extraction_text")
        if isinstance(cls, str) and cls in ontology.relation_types:
            relation_seen[cls] = relation_seen.get(cls, 0) + 1
            continue
        start = item.get("start_pos")
        end = item.get("end_pos")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and not isinstance(start, bool)
            and not isinstance(end, bool)
            and 0 <= start < end <= len(text)
            and isinstance(mention, str)
        ):
            if text[start:end] != mention:
                defects.append(f"example {index} offsets do not match '{mention}'")
            else:
                occurrences.setdefault(mention, []).append((start, end))
        else:
            defects.append(f"example {index} entity {cls} has bad offsets")
        if not isinstance(cls, str) or cls not in ontology.entity_types:
            defects.append(f"example {index} has undeclared entity type {cls}")
    for item in items:
        cls = item.get("extraction_class")
        if isinstance(cls, str) and cls in ontology.relation_types:
            _relation_defects(index, cls, item, occurrences, defects)


def _relation_defects(
    index: int,
    relation: str,
    item: dict[str, object],
    occurrences: dict[str, list[tuple[int, int]]],
    defects: list[str],
) -> None:
    attrs = item.get("attributes")
    if not isinstance(attrs, dict):
        defects.append(f"example {index} relation {relation} has no attributes")
        return
    subject = attrs.get("subject")
    obj = attrs.get("object")
    confidence = attrs.get("confidence")
    if subject not in occurrences:
        defects.append(f"example {index} relation {relation} subject not extracted")
    if obj not in occurrences:
        defects.append(f"example {index} relation {relation} object not extracted")
    if subject in occurrences and obj in occurrences:
        subject_end = occurrences[subject][0][1]
        distinct_object = [span for span in occurrences[obj] if span[0] >= subject_end]
        if not distinct_object:
            defects.append(f"example {index} relation {relation} endpoints overlap")
    confidence_ok = False
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        confidence_ok = 0.0 <= float(confidence) <= 1.0
    if not confidence_ok:
        defects.append(f"example {index} relation {relation} confidence out of range")
    phrase = item.get("extraction_text")
    if not isinstance(phrase, str) or not phrase.strip():
        defects.append(f"example {index} relation {relation} has an empty predicate")
