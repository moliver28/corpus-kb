"""Entity- and relation-level ensemble voting across extractors.

Adopts the extraction-ensemble finding (issue #133): run two or more
extractors over the same chunks and keep only candidates on which the
members agree. Agreement is compared on normalized keys —
``(name.lower(), entity_type)`` for entities, ``(source name.lower(),
relation_type, target name.lower())`` for relations — so span offsets
and member-local entity ids never split a true agreement. Kept
candidates take the highest-confidence member instance, gain a
unanimous-agreement confidence bonus, and are re-attributed to
``extractor_id="ensemble"``.

A member that raises is recorded in ``stats.notes`` and skipped; the
ensemble fails only when every member fails, so one broken backend can
never take the whole ingest down.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation
from .protocol import Extractor, ExtractStats

UNANIMOUS_BONUS = 0.1

EntityVotes = defaultdict[tuple[str, str], list[Entity]]
RelationVotes = defaultdict[tuple[str, str, str], list[Relation]]


class EnsembleExtractor:
    """Majority-vote composite over member extractors."""

    extractor_id: str = "ensemble"

    def __init__(self, members: list[Extractor]) -> None:
        if len(members) < 2:
            raise ValueError("EnsembleExtractor needs at least two members")
        self._members = members

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[Relation], ExtractStats]:
        entity_votes: EntityVotes = defaultdict(list)
        relation_votes: RelationVotes = defaultdict(list)
        merged = ExtractStats(backend=self.extractor_id)
        alive = 0
        failures: list[str] = []
        for member in self._members:
            try:
                entities, relations, stats = member.extract(chunks, ontology, source_document_id)
            except Exception as exc:
                failures.append(f"{member.extractor_id}: {type(exc).__name__}: {exc}")
                continue
            alive += 1
            names = _dedupe_entities(entity_votes, entities)
            _collect_relations(relation_votes, relations, names)
            merged = _merge(merged, stats)

        if alive == 0:
            raise RuntimeError(f"all ensemble members failed: {'; '.join(failures)}")
        needed = min(2, alive)
        kept_entities = _elect_entities(entity_votes, needed, alive, source_document_id)
        kept_relations = _elect_relations(relation_votes, needed, alive)
        notes = list(failures)
        notes.append(
            f"ensemble kept {len(kept_entities)} entity and "
            f"{len(kept_relations)} relation votes at >= {needed} of {alive} members"
        )
        merged = replace(
            merged,
            kept_entities=len(kept_entities),
            kept_relations=len(kept_relations),
            dropped_by_other=merged.dropped_by_other
            + (merged.requested_entities - len(kept_entities))
            + (merged.requested_relations - len(kept_relations)),
            notes=tuple(notes),
        )
        return kept_entities, kept_relations, merged


def _dedupe_entities(
    votes: dict[tuple[str, str], list[Entity]], entities: list[Entity]
) -> dict[str, str]:
    """Add one member's entities to the vote pool; return id -> name."""
    seen: set[tuple[str | None, str | None, str]] = set()
    names: dict[str, str] = {}
    for entity in entities:
        span = (entity.chunk_id, entity.source_start_char, entity.source_end_char)
        key = (entity.name.strip().lower(), entity.entity_type)
        if key in votes and span in seen:
            continue
        seen.add(span)
        votes[key].append(entity)
        if entity.entity_id:
            names[entity.entity_id] = entity.name
    return names


def _collect_relations(
    votes: dict[tuple[str, str, str], list[Relation]],
    relations: list[Relation],
    names: dict[str, str],
) -> None:
    for relation in relations:
        source = names.get(relation.source_entity_id, relation.source_entity_id)
        target = names.get(relation.target_entity_id, relation.target_entity_id)
        votes[(source.strip().lower(), relation.relation_type, target.strip().lower())].append(
            relation
        )


def _elect_entities(
    votes: dict[tuple[str, str], list[Entity]],
    needed: int,
    alive: int,
    source_document_id: str,
) -> list[Entity]:
    elected: list[Entity] = []
    for candidates in votes.values():
        if len(candidates) < needed:
            continue
        best = max(candidates, key=lambda e: e.confidence or 0.0)
        bonus = UNANIMOUS_BONUS if len(candidates) == alive else 0.0
        elected.append(
            best.model_copy(
                update={
                    "confidence": min(0.99, (best.confidence or 0.0) + bonus),
                    "extractor_id": "ensemble",
                    "source_document_id": source_document_id,
                    "metadata": {**best.metadata, "votes": len(candidates)},
                }
            )
        )
    return elected


def _elect_relations(
    votes: dict[tuple[str, str, str], list[Relation]], needed: int, alive: int
) -> list[Relation]:
    elected: list[Relation] = []
    for candidates in votes.values():
        if len(candidates) < needed:
            continue
        best = max(candidates, key=lambda r: r.confidence or 0.0)
        bonus = UNANIMOUS_BONUS if len(candidates) == alive else 0.0
        elected.append(
            best.model_copy(
                update={
                    "confidence": min(0.99, (best.confidence or 0.0) + bonus),
                    "extractor_id": "ensemble",
                    "metadata": {**best.metadata, "votes": len(candidates)},
                }
            )
        )
    return elected


def _merge(acc: ExtractStats, stats: ExtractStats) -> ExtractStats:
    return ExtractStats(
        backend=acc.backend,
        requested_entities=acc.requested_entities + stats.requested_entities,
        kept_entities=acc.kept_entities + stats.kept_entities,
        requested_relations=acc.requested_relations + stats.requested_relations,
        kept_relations=acc.kept_relations + stats.kept_relations,
        dropped_by_cap=acc.dropped_by_cap + stats.dropped_by_cap,
        dropped_by_confidence=acc.dropped_by_confidence + stats.dropped_by_confidence,
        dropped_by_other=acc.dropped_by_other + stats.dropped_by_other,
        duration_ms=acc.duration_ms + stats.duration_ms,
        notes=acc.notes + stats.notes,
    )
