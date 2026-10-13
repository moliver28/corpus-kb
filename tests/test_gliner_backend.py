"""Tests for the GLiNER2 backend — config plumbing, factory routing, and
span normalization against a canned fake model (never the live model;
gliner2 is not installed in CI)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from corpus_kb.extraction import create_extractor
from corpus_kb.extraction.gliner_backend import (
    DEFAULT_GLINER_MODEL,
    DEFAULT_GLINER_REVISION,
    MIN_SPAN_CONFIDENCE,
    TYPE_DEFINITIONS,
    GlinerExtractor,
)
from corpus_kb.ontology import load_ontology
from corpus_kb.utils.models import Chunk

_ONTOLOGY_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "corpus_kb" / "config" / "ontology.yaml"
)


class _FakeGlinerModel:
    """Returns canned schema payloads; records the schema it was given."""

    def __init__(self, buckets: dict[str, list[object]]) -> None:
        self._buckets = buckets
        self.seen_schema: dict[str, str] | None = None

    def extract_entities(self, text: str, schema: dict[str, str]) -> dict[str, list[object]]:
        self.seen_schema = schema
        return self._buckets


def _extractor_with(buckets: dict[str, list[object]], **kwargs: Any) -> GlinerExtractor:
    extractor = GlinerExtractor(**kwargs)
    extractor._model = _FakeGlinerModel(buckets)
    return extractor


def _chunk(text: str) -> Chunk:
    return Chunk(chunk_id="c1", document_id="d1", text=text, source_type="text")


# ---------------------------------------------------------------------------
# Constructor / config plumbing
# ---------------------------------------------------------------------------


def test_constructor_defaults_to_six_pinned_types() -> None:
    extractor = GlinerExtractor()
    assert extractor._types == [
        "person",
        "organization",
        "location",
        "date",
        "money",
        "product",
    ]
    assert extractor._model_id == DEFAULT_GLINER_MODEL
    assert extractor._revision == DEFAULT_GLINER_REVISION


def test_constructor_rejects_unknown_mention_type() -> None:
    with pytest.raises(ValueError, match="Unknown gliner mention types"):
        GlinerExtractor(types=["person", "bogus"])


def test_constructor_subset_limits_schema() -> None:
    extractor = _extractor_with({}, types=["person", "money"])
    assert extractor._schema == {
        "person": TYPE_DEFINITIONS["person"],
        "money": TYPE_DEFINITIONS["money"],
    }


def test_factory_routes_gliner_when_enabled() -> None:
    config: dict[str, object] = {
        "graph": {"extractor": "gliner"},
        "extraction": {
            "gliner": {"enabled": True, "model": "fastino/gliner2-large-v1", "revision": "main"}
        },
    }
    extractor = create_extractor(config)
    assert isinstance(extractor, GlinerExtractor)
    assert extractor._model_id == "fastino/gliner2-large-v1"
    assert extractor._revision == "main"


def test_factory_refuses_gliner_while_disabled() -> None:
    config: dict[str, object] = {
        "graph": {"extractor": "gliner"},
        "extraction": {"gliner": {"enabled": False}},
    }
    with pytest.raises(ValueError, match="gliner extractor is opt-in"):
        create_extractor(config)


# ---------------------------------------------------------------------------
# Span normalization + honest counting (canned fake model)
# ---------------------------------------------------------------------------

_TEXT = "Alice visited Acme in Berlin on 2019."


def test_extract_maps_spans_to_ontology_and_records_provenance() -> None:
    buckets: dict[str, list[object]] = {
        "person": [{"text": "Alice", "start": 0, "end": 5, "score": 0.9, "label": "person"}],
        "organization": [
            SimpleNamespace(text="Acme", start=14, end=18, score=0.8, label="organization")
        ],
    }
    extractor = _extractor_with(buckets)
    ontology = load_ontology(_ONTOLOGY_PATH)

    entities, relations, _stats = extractor.extract([_chunk(_TEXT)], ontology, "doc-g")

    assert [e.name for e in entities] == ["Alice", "Acme"]
    assert entities[0].entity_type == "Person"
    assert entities[1].entity_type == "Org"  # synonym mapping
    assert entities[0].confidence == pytest.approx(0.9)
    metadata = entities[0].metadata
    assert metadata["extractor"] == "gliner"
    assert metadata["model"] == DEFAULT_GLINER_MODEL
    assert metadata["revision"] == DEFAULT_GLINER_REVISION
    assert metadata["confidence"] == pytest.approx(0.9)
    assert metadata["gliner_type"] == "person"
    assert relations == []


def test_extract_drops_low_confidence_and_out_of_range_spans_honestly() -> None:
    low = MIN_SPAN_CONFIDENCE - 0.01
    buckets: dict[str, list[object]] = {
        "person": [
            {"text": "Alice", "start": 0, "end": 5, "score": 0.9, "label": "person"},
            {"text": "Whisper", "start": 0, "end": 7, "score": low, "label": "person"},
            {"text": "Overflow", "start": 0, "end": 999, "score": 0.95, "label": "person"},
        ],
    }
    extractor = _extractor_with(buckets)
    ontology = load_ontology(_ONTOLOGY_PATH)

    entities, _relations, stats = extractor.extract([_chunk(_TEXT)], ontology, "doc-g")

    assert len(entities) == 1
    assert stats.requested_entities == 3
    assert stats.kept_entities == 1
    assert stats.dropped_by_confidence == 1
    assert stats.dropped_by_other == 1


def test_extract_maps_unmappable_types_to_concept_and_counts_none_dropped() -> None:
    buckets: dict[str, list[object]] = {
        "location": [{"text": "Berlin", "start": 22, "end": 28, "score": 0.9, "label": "location"}],
    }
    extractor = _extractor_with(buckets)
    ontology = load_ontology(_ONTOLOGY_PATH)

    entities, _relations, stats = extractor.extract([_chunk(_TEXT)], ontology, "doc-g")

    assert entities[0].entity_type == "Concept"  # no Place/Location in default ontology
    assert stats.dropped_by_confidence == 0
    assert stats.dropped_by_other == 0
    assert stats.requested_entities == stats.kept_entities == 1


def test_extract_reports_no_relation_extraction_in_notes() -> None:
    extractor = _extractor_with({})
    ontology = load_ontology(_ONTOLOGY_PATH)

    _entities, relations, stats = extractor.extract([_chunk(_TEXT)], ontology, "doc-g")

    assert relations == []
    assert stats.requested_relations == 0
    assert stats.kept_relations == 0
    assert any("relation" in note for note in stats.notes)


def test_extract_passes_one_line_definitions_as_schema() -> None:
    model = _FakeGlinerModel({})
    extractor = GlinerExtractor()
    extractor._model = model
    ontology = load_ontology(_ONTOLOGY_PATH)

    extractor.extract([_chunk(_TEXT)], ontology, "doc-g")

    assert model.seen_schema is not None
    assert set(model.seen_schema) == set(TYPE_DEFINITIONS)
    assert all(isinstance(desc, str) and desc for desc in model.seen_schema.values())
