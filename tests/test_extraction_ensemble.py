"""Tests for EnsembleExtractor voting (issue #133 adoption)."""

from __future__ import annotations

import pytest

from corpus_kb.extraction.ensemble import EnsembleExtractor
from corpus_kb.extraction.protocol import ExtractStats
from corpus_kb.ontology import Ontology
from corpus_kb.utils.models import Chunk, Entity, Relation


def _ontology() -> Ontology:
    return Ontology(entity_types=["PERSON"], relation_types=["LIKES"])


def _chunk() -> Chunk:
    return Chunk(document_id="doc-1", text="Elena likes ice cream.", source_type="text")


class _StaticExtractor:
    def __init__(
        self,
        extractor_id: str,
        entities: list[Entity],
        relations: list[Relation] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.extractor_id = extractor_id
        self._entities = entities
        self._relations = relations or []
        self._error = error

    def extract(
        self, chunks: list[Chunk], ontology: Ontology, source_document_id: str
    ) -> tuple[list[Entity], list[Relation], ExtractStats]:
        if self._error is not None:
            raise self._error
        return (
            self._entities,
            self._relations,
            ExtractStats(
                backend=self.extractor_id,
                requested_entities=len(self._entities),
                kept_entities=len(self._entities),
                requested_relations=len(self._relations),
                kept_relations=len(self._relations),
            ),
        )


def _entity(name: str = "Elena", confidence: float = 0.8) -> Entity:
    return Entity(
        name=name,
        entity_type="PERSON",
        source_type="text",
        source_document_id="doc-1",
        confidence=confidence,
        extractor_id="member",
    )


def test_agreement_keeps_with_unanimous_bonus() -> None:
    members = [
        _StaticExtractor("a", [_entity(confidence=0.8)]),
        _StaticExtractor("b", [_entity(confidence=0.6)]),
    ]
    entities, _relations, stats = EnsembleExtractor(members).extract(
        [_chunk()], _ontology(), "doc-1"
    )
    assert len(entities) == 1
    assert entities[0].extractor_id == "ensemble"
    assert entities[0].confidence == pytest.approx(0.9)  # 0.8 + unanimous 0.1, cap 0.99
    assert stats.kept_entities == 1
    assert stats.requested_entities == 2


def test_disagreement_drops_and_relations_vote_on_names() -> None:
    members = [
        _StaticExtractor("a", [_entity("Elena")], [_relation("Elena", "ice cream")]),
        _StaticExtractor("b", [_entity("Miguel")], [_relation("Miguel", "pie")]),
    ]
    entities, relations, stats = EnsembleExtractor(members).extract(
        [_chunk()], _ontology(), "doc-1"
    )
    assert entities == []
    assert relations == []
    assert stats.dropped_by_other > 0


def test_member_failure_is_recorded_and_majority_still_applies() -> None:
    members = [
        _StaticExtractor("a", [_entity()], error=RuntimeError("boom")),
        _StaticExtractor("b", [_entity(confidence=0.7)]),
    ]
    entities, _relations, stats = EnsembleExtractor(members).extract(
        [_chunk()], _ontology(), "doc-1"
    )
    assert len(entities) == 1
    assert any("a: RuntimeError" in note for note in stats.notes)


def test_all_members_failing_raises() -> None:
    members = [
        _StaticExtractor("a", [], error=RuntimeError("x")),
        _StaticExtractor("b", [], error=RuntimeError("y")),
    ]
    with pytest.raises(RuntimeError, match="all ensemble members failed"):
        EnsembleExtractor(members).extract([_chunk()], _ontology(), "doc-1")


def test_requires_two_members() -> None:
    with pytest.raises(ValueError, match="at least two members"):
        EnsembleExtractor([_StaticExtractor("a", [])])


def _relation(source: str, target: str) -> Relation:
    return Relation(
        source_entity_id=f"{source}-id",
        target_entity_id=f"{target}-id",
        relation_type="LIKES",
        confidence=0.7,
        extractor_id="member",
    )
