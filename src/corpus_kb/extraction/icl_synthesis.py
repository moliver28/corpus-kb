"""Self-prompting ICL: few-shot examples synthesized from the ontology.

The langextract backend used one hand-written example pair for the first
two entity types and the first relation type only. This module builds one
synthetic example per relation type straight from the live ``Ontology``,
so the examples always match the ontology and adapt when it grows.

Determinism contract (program standard 9): the same ontology content
yields byte-identical examples. Iteration follows the ontology's own
type order; the only choice points (domain/range pairs for relation
names with no hint) draw from a SHA-256 stream keyed on the ontology
fingerprint, so one ontology always yields one prompt.

Honesty contract: the batch is validated before use. A degenerate batch
(empty, duplicated, bad offsets, missing relation signature) is rejected
and the legacy static examples are used instead; the receipt note on
``ExtractStats.notes`` says which path produced the prompt. A cache
keyed on the ontology fingerprint reuses examples across runs and says
so in the receipt.
"""

from __future__ import annotations

import hashlib
import json
import logging

from ..ontology import Ontology
from ..utils.deterministic import HashRandom
from .icl_validate import exemplar_defects

logger = logging.getLogger(__name__)

_CACHE_MAX = 8
# Ontology fingerprint -> validated exemplar batch. Treat batches as
# read-only; callers never mutate them.
_CACHE: dict[str, tuple[dict[str, object], ...]] = {}

#: Preferred (subject, object) entity types for known relation names.
#: Unknown names, and hints absent from the live vocabulary, fall back to
#: a seeded deterministic pick so a grown ontology still gets examples.
_PAIR_HINTS: dict[str, tuple[str, str]] = {
    "PART_OF": ("Section", "Document"),
    "MENTIONS": ("Document", "Person"),
    "DEFINED_AS": ("Section", "Concept"),
    "AUTHORED_BY": ("Document", "Person"),
    "CITES": ("Document", "Document"),
    "SUPPORTS": ("Claim", "Claim"),
    "CONTRADICTS": ("Claim", "Claim"),
    "RELATED_TO": ("Concept", "Concept"),
    "INSTANCE_OF": ("Product", "Product"),
    "DEPENDS_ON": ("Product", "Product"),
    "CALLS": ("Chunk", "Chunk"),
}


def ontology_fingerprint(ontology: Ontology) -> str:
    """SHA-256 of the ontology's full declared content."""
    payload = {
        "version": ontology.version,
        "entity_types": ontology.entity_types,
        "relation_types": ontology.relation_types,
        "entity_descriptions": ontology.entity_descriptions,
        "relation_descriptions": ontology.relation_descriptions,
        "relation_graph_eligible": ontology.relation_graph_eligible,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def clear_examples_cache() -> None:
    """Drop all cached example batches (call after an ontology reload)."""
    _CACHE.clear()


def _predicate_phrase(ontology: Ontology, relation: str) -> str:
    """The relation's predicate text: its description, else its name."""
    description = ontology.relation_descriptions.get(relation, "")
    phrase = description.strip().rstrip(".").strip() if description else ""
    if not phrase:
        phrase = relation.lower().replace("_", " ").strip()
    return phrase


def _pair_for(ontology: Ontology, relation: str, fingerprint: str) -> tuple[str, str]:
    """Deterministic (subject, object) entity types for one relation."""
    types = ontology.entity_types
    hint = _PAIR_HINTS.get(relation)
    if hint is not None and hint[0] in types and hint[1] in types:
        return hint
    rng = HashRandom(f"{fingerprint}:{relation}")
    first = int(rng.uniform(0, len(types)))
    second = int(rng.uniform(0, len(types)))
    if len(types) > 1 and first == second:
        second = (second + 1) % len(types)
    return types[first], types[second]


def synthesize_exemplars(ontology: Ontology) -> tuple[dict[str, object], ...]:
    """One entity-pair example per relation type, in ontology order."""
    fingerprint = ontology_fingerprint(ontology)
    exemplars: list[dict[str, object]] = []
    for relation in ontology.relation_types:
        exemplars.append(_exemplar_for(ontology, relation, fingerprint))
    return tuple(exemplars)


def _exemplar_for(ontology: Ontology, relation: str, fingerprint: str) -> dict[str, object]:
    subject_type, object_type = _pair_for(ontology, relation, fingerprint)
    subject = f"Example{subject_type}"
    obj = f"Example{object_type}"
    phrase = _predicate_phrase(ontology, relation)
    text = f"{subject} {phrase} {obj}."
    object_start = text.find(obj, len(subject))
    extractions: list[object] = [
        {
            "extraction_class": subject_type,
            "extraction_text": subject,
            "start_pos": 0,
            "end_pos": len(subject),
        },
        {
            "extraction_class": object_type,
            "extraction_text": obj,
            "start_pos": object_start,
            "end_pos": object_start + len(obj) if object_start >= 0 else None,
        },
        {
            "extraction_class": relation,
            "extraction_text": phrase,
            "attributes": {"subject": subject, "object": obj, "confidence": 0.9},
        },
    ]
    return {"text": text, "extractions": extractions}


def static_exemplar_dicts(ontology: Ontology) -> tuple[dict[str, object], ...]:
    """The static fallback set, as plain dicts (the legacy example shape).

    Kept as the safe harbor when synthesis is rejected. Unlike the legacy
    builder, the entity example's span points at its own mention text so
    every fallback example passes the offset round-trip check.
    """
    examples: list[dict[str, object]] = []
    for entity_type in ontology.entity_types[:2]:
        text = f"Example of {entity_type}."
        start = text.index(entity_type)
        examples.append(
            {
                "text": text,
                "extractions": [
                    {
                        "extraction_class": entity_type,
                        "extraction_text": entity_type,
                        "start_pos": start,
                        "end_pos": start + len(entity_type),
                    }
                ],
            }
        )
    if ontology.relation_types and len(ontology.entity_types) >= 2:
        subject_type, object_type = ontology.entity_types[0], ontology.entity_types[1]
        subject, obj = f"Example{subject_type}", f"Example{object_type}"
        text = f"{subject} relates to {obj}."
        object_start = text.index(obj)
        examples.append(
            {
                "text": text,
                "extractions": [
                    {
                        "extraction_class": subject_type,
                        "extraction_text": subject,
                        "start_pos": 0,
                        "end_pos": len(subject),
                    },
                    {
                        "extraction_class": object_type,
                        "extraction_text": obj,
                        "start_pos": object_start,
                        "end_pos": object_start + len(obj),
                    },
                    {
                        "extraction_class": ontology.relation_types[0],
                        "extraction_text": "relates to",
                        "attributes": {
                            "subject": subject,
                            "object": obj,
                            "confidence": 0.9,
                        },
                    },
                ],
            }
        )
    return tuple(examples)


def few_shot_examples(
    ontology: Ontology,
) -> tuple[tuple[dict[str, object], ...], str]:
    """The exemplar batch for this ontology plus an honest receipt note.

    Cached by ontology fingerprint: unchanged ontology reuses the batch
    (receipt says "reused"); a changed ontology regenerates it ("built");
    a degenerate synthesis falls back to the static set ("static").
    """
    fingerprint = ontology_fingerprint(ontology)
    cached = _CACHE.get(fingerprint)
    if cached is not None:
        return cached, (
            f"Few-shot examples: reused {len(cached)} cached examples "
            f"for ontology {fingerprint[:8]}."
        )
    batch, note = _adopt_batch(synthesize_exemplars(ontology), ontology, fingerprint)
    if exemplar_defects(batch, ontology) == []:
        # Cache only batches that pass validation. The static fallback
        # stays uncached, so its receipt stays accurate on every run.
        _CACHE[fingerprint] = batch
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
    return batch, note


def _adopt_batch(
    batch: tuple[dict[str, object], ...],
    ontology: Ontology,
    fingerprint: str,
) -> tuple[tuple[dict[str, object], ...], str]:
    """Validate one freshly synthesized batch or fall back to static."""
    defects = exemplar_defects(batch, ontology)
    if defects:
        logger.warning(
            "ICL synthesis rejected for ontology %s: %s",
            fingerprint[:8],
            "; ".join(defects),
        )
        return static_exemplar_dicts(ontology), (
            f"Few-shot examples: synthesis check failed ({defects[0]}); using static examples."
        )
    return batch, (
        f"Few-shot examples: built {len(batch)} examples from ontology {fingerprint[:8]}."
    )
