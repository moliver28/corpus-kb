"""Tests for the composite ML extractor (ml_backend + relex_backend).

The gliner2 and gliner packages are faked at their lazy-import seams
(``gliner_backend.import_gliner2`` / ``relex_backend.import_gliner``) so
no test ever downloads weights.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.extraction.gliner_backend import GlinerExtractor
from corpus_kb.extraction.ml_backend import MLExtractor, extraction_mode
from corpus_kb.extraction.relex_backend import RelexExtractor
from corpus_kb.ontology import Ontology
from corpus_kb.utils.models import Chunk, Entity, Relation


def build_ontology() -> Ontology:
    return Ontology(
        entity_types=["PERSON", "ORG", "CONCEPT", "PLACE"],
        relation_types=["LIKES"],
        relation_descriptions={"LIKES": "likes"},
    )


class _FakeGliner2Model:
    def extract_entities(self, text: str, schema: dict[str, str]) -> dict[str, list[object]]:
        return {
            "person": [{"text": "Elena", "start": 0, "end": 5, "score": 0.92, "label": "person"}]
        }


class _FakeGliner2Module:
    class GLiNER2:
        @staticmethod
        def from_pretrained(model_id: str, revision: str | None = None) -> _FakeGliner2Model:
            return _FakeGliner2Model()


class _FakeRelexModel:
    def predict_relations(
        self,
        text: str,
        labels: list[tuple[str, str]],
        entities: list[str] | None = None,
        threshold: float = 0.5,
    ) -> list[object]:
        assert labels
        return [
            {
                "head": {"text": "Elena", "start": 0, "end": 5, "label": "person"},
                "tail": {"text": "ice cream", "start": 18, "end": 28, "label": "product"},
                "label": "likes",
                "score": 0.88,
            }
        ]


class _FakeGlinerModule:
    class GLiNER:
        @staticmethod
        def from_pretrained(model_id: str) -> _FakeRelexModel:
            return _FakeRelexModel()


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.extraction.gliner_backend as gb
    import corpus_kb.extraction.relex_backend as rb

    monkeypatch.setattr(gb, "import_gliner2", lambda: _FakeGliner2Module())
    monkeypatch.setattr(rb, "import_gliner", lambda: _FakeGlinerModule())


def _chunk(text: str = "Elena loves ice cream.") -> Chunk:
    return Chunk(document_id="doc-1", text=text, source_type="text")


def _ml_config(mode: str = "zero_shot") -> dict[str, Any]:
    return {
        "extraction": {
            "backend": "ml",
            "mode": mode,
            "gliner": {
                "enabled": False,
                "model": "fastino/gliner2-large-v1",
                "revision": "main",
                "types": ["person", "product"],
            },
            "relex": {
                "model": "knowledgator/gliner-relex-large-v1.0",
                "threshold": 0.5,
                "max_label_pairs": 40,
            },
        }
    }


def test_extraction_mode_default_and_unknown() -> None:
    assert extraction_mode({}) == "zero_shot"
    with pytest.raises(ValueError, match="Unknown extraction mode"):
        extraction_mode({"extraction": {"mode": "bogus"}})


def test_ml_zero_shot_merges_entities_and_linked_relations() -> None:
    extractor = MLExtractor(_ml_config())
    ontology = build_ontology()
    entities, relations, stats = extractor.extract([_chunk()], ontology, "doc-1")

    # gliner emits Elena; relex links its head to that entity and emits one
    # pseudo-entity for the unlinked tail span so the relation resolves.
    assert [e.name for e in entities] == ["Elena", "ice cream"]
    assert stats.backend == "ml"
    assert stats.kept_relations == 1
    relation = relations[0]
    assert relation.relation_type == "LIKES"
    assert relation.extractor_id == "relex"
    names = {e.entity_id: e.name for e in entities}
    assert names[relation.source_entity_id] == "Elena"
    assert names[relation.target_entity_id] == "ice cream"


def test_ml_customized_without_exemplars_degrades_honestly() -> None:
    config = _ml_config("customized")
    config["graph"] = {
        "fixture_dir": "tests/fixtures/langextract_recorded",
        "live_fallback": False,
    }
    extractor = MLExtractor(config)
    entities, _relations, stats = extractor.extract([_chunk()], build_ontology(), "doc-1")
    assert any("no exemplars" in note for note in stats.notes)
    assert entities == []


def test_ml_few_shot_uses_langextract_member() -> None:
    extractor = MLExtractor(_ml_config("few_shot"))
    assert len(extractor._members) == 1
    assert extractor._members[0].extractor_id == "langextract"


def test_relex_unknown_relation_type_is_dropped_honestly() -> None:
    extractor = RelexExtractor()
    ontology = Ontology(
        entity_types=["PERSON"],
        relation_types=["DISLIKES"],  # fake emits LIKES only
    )
    _entities, relations, stats = extractor.extract([_chunk()], ontology, "doc-1")
    assert relations == []
    assert stats.dropped_by_other == 1


def test_gliner_extractor_loads_faked_model() -> None:
    extractor = GlinerExtractor(types=["person"])
    entities, relations, stats = extractor.extract([_chunk()], build_ontology(), "doc-1")
    assert entities[0].name == "Elena"
    assert entities[0].entity_type == "PERSON"
    assert relations == []
    assert stats.kept_entities == 1


@pytest.mark.asyncio
async def test_ml_fallback_chain_notes_empty_ml(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty ml result falls through langextract to regex with notes."""
    import corpus_kb.tools.ingest_extract as ingest_extract
    from corpus_kb.extraction.protocol import ExtractStats

    class _EmptyML:
        extractor_id = "ml"

        def extract(
            self, chunks: list[Chunk], ontology: Ontology, source_document_id: str
        ) -> tuple[list[Entity], list[Relation], ExtractStats]:
            return [], [], ExtractStats(backend="ml")

    monkeypatch.setattr(ingest_extract, "create_extractor", lambda config, pool=None: _EmptyML())
    _entities, _relations, extractor_id, stats = await ingest_extract.extract_with_fallback(
        [_chunk()], build_ontology(), "doc-1", _ml_config()
    )
    assert extractor_id == "regex"
    assert any("ml returned no candidates" in note for note in stats.notes)


def test_create_factory_resolves_ml() -> None:
    from corpus_kb.extraction import create_extractor

    extractor = create_extractor(_ml_config())
    assert extractor.extractor_id == "ml"
