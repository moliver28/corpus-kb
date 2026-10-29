"""Graph write policy routing at the one relation-write call site.

Golden-first: the ``mode: all`` tests pin today's write sequence — every
extracted relation reaches the store, in extraction order, with the stage's
returned relations complete. They stay green across the Wave 2 change.

The ``semantic_only`` tests pin the Wave 2 contract: only the relation
types the ontology marks ``graph_eligible`` reach the store; the rest are
dropped deterministically (stable input order, no reordering). Extraction
truth stays complete either way — the ingest result envelope is untouched.

No database, no Ollama: the store is a recording fake sink.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from corpus_kb.extraction import ExtractStats
from corpus_kb.ontology import Ontology
from corpus_kb.tools import ingest_common
from corpus_kb.tools.pipeline.extract import _stage_extract_entities
from corpus_kb.utils.models import Chunk, Entity, Relation


@dataclass
class _RecordingSink:
    """Fake IngestSink: records every relation write in call order."""

    writes: list[list[Relation]] = field(default_factory=list)
    entity_writes: list[list[Entity]] = field(default_factory=list)

    @property
    def tenant_id(self) -> str:
        return "00000000-0000-0000-0000-000000000001"

    async def store_entities(self, entities: list[Entity]) -> dict[str, str]:
        self.entity_writes.append(list(entities))
        return {entity.entity_id: entity.entity_id for entity in entities}

    async def store_relations(
        self,
        relations: list[Relation],
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> int:
        self.writes.append(list(relations))
        return len(relations)


def _relation(relation_type: str, target: str) -> Relation:
    return Relation(
        relation_id=f"rel-{relation_type}-{target}",
        source_entity_id="ent-a",
        target_entity_id=target,
        relation_type=relation_type,
    )


def _ontology() -> Ontology:
    """Two types; MENTIONS is not graph-eligible (the Wave 1 flag)."""
    return Ontology.model_validate(
        {
            "entity_types": ["concept"],
            "relation_types": ["LINKS_TO", "MENTIONS"],
            "relation_graph_eligible": {"MENTIONS": False},
        }
    )


def _stage_setup(
    monkeypatch: pytest.MonkeyPatch,
    relations: list[Relation],
) -> _RecordingSink:
    """Run stage 4 with a fixed extractor payload and recording sink."""
    sink = _RecordingSink()
    entity = Entity(entity_id="ent-a", name="A", entity_type="concept", source_type="text")

    async def fake_extract(
        chunks: list[Chunk],
        ontology: Ontology,
        document_id: str,
        config: dict[str, Any],
        pool: object = None,
    ) -> tuple[list[Entity], list[Relation], str, ExtractStats]:
        return [entity], relations, "regex", ExtractStats(backend="regex")

    monkeypatch.setattr(ingest_common, "extract_with_fallback", fake_extract)
    monkeypatch.setattr(ingest_common, "ontology", lambda config: _ontology())
    return sink


def _config(mode: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {"graph": {"extract_entities": True}}
    if mode is not None:
        config["graph_policy"] = {"mode": mode}
    return config


_CHUNK = Chunk(document_id="doc-1", text="A links to B.", source_type="text")


async def test_golden_mode_all_writes_every_relation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Golden: mode all (default) writes all relations, order preserved."""
    relations = [_relation("MENTIONS", "ent-b"), _relation("LINKS_TO", "ent-c")]
    sink = _stage_setup(monkeypatch, relations)

    _entity, returned, _extractor, _stats = await _stage_extract_entities(
        sink, [_CHUNK], "doc-1", _config("all"), None, []
    )

    assert len(sink.writes) == 1
    assert sink.writes[0] == relations
    assert returned == relations


async def test_golden_absent_mode_writes_every_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Golden: no graph_policy key at all behaves exactly like mode all."""
    relations = [_relation("MENTIONS", "ent-b"), _relation("LINKS_TO", "ent-c")]
    sink = _stage_setup(monkeypatch, relations)

    _entity, returned, _extractor, _stats = await _stage_extract_entities(
        sink, [_CHUNK], "doc-1", _config(None), None, []
    )

    assert len(sink.writes) == 1
    assert sink.writes[0] == relations
    assert returned == relations


async def test_semantic_only_keeps_eligible_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """semantic_only: ineligible types never reach the store; order stable."""
    mentions = _relation("MENTIONS", "ent-b")
    links = _relation("LINKS_TO", "ent-c")
    mentions2 = _relation("MENTIONS", "ent-d")
    sink = _stage_setup(monkeypatch, [mentions, links, mentions2])

    _entity, returned, _extractor, _stats = await _stage_extract_entities(
        sink, [_CHUNK], "doc-1", _config("semantic_only"), None, []
    )

    assert len(sink.writes) == 1
    assert sink.writes[0] == [links]
    assert returned == [mentions, links, mentions2]


async def test_semantic_only_all_ineligible_skips_the_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """semantic_only: with no eligible type left, the store is not called."""
    relations = [_relation("MENTIONS", "ent-b"), _relation("MENTIONS", "ent-c")]
    sink = _stage_setup(monkeypatch, relations)

    _entity, returned, _extractor, _stats = await _stage_extract_entities(
        sink, [_CHUNK], "doc-1", _config("semantic_only"), None, []
    )

    assert sink.writes == []
    assert returned == relations
