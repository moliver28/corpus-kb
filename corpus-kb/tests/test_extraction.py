"""Tests for the pluggable ontology extractor seam."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from unittest.mock import MagicMock

from src.extraction import (
    LangExtractExtractor,
    OntologyViolationError,
    RegexExtractor,
    create_extractor,
)
from src.ontology import load_ontology
from src.utils.models import Chunk

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "langextract_recorded"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestRegexExtractor:
    def test_regex_extractor_returns_entities_with_expected_provenance(
        self,
    ) -> None:
        """Given markdown chunks, RegexExtractor emits regex-sourced entities."""
        ontology = load_ontology("config/ontology.yaml")
        chunk = Chunk(
            chunk_id="chunk-regex",
            document_id="doc-regex",
            text="# UserService\nThe UserService handles authentication.",
            source_type="markdown",
        )

        extractor = RegexExtractor()
        entities, relations = extractor.extract(
            [chunk], ontology, source_document_id="doc-regex"
        )

        assert len(entities) >= 1
        assert len(relations) == 0
        names = {entity.name for entity in entities}
        assert "UserService" in names
        for entity in entities:
            assert entity.extractor_id == "regex"
            assert entity.confidence is None
            assert entity.source_document_id == "doc-regex"
            assert entity.entity_type in ontology.entity_types


class TestLangExtractExtractor:
    def test_recorded_fixture_returns_entities_and_relations(self) -> None:
        """Given recorded fixtures, LangExtractExtractor emits typed entities/relations."""
        ontology = load_ontology("config/ontology.yaml")
        text = "Alice Smith works at Acme Corporation in New York."
        chunk = Chunk(
            chunk_id="chunk-alice",
            document_id="doc-alice",
            text=text,
            source_type="text",
        )

        extractor = LangExtractExtractor(
            fixture_dir=_FIXTURE_DIR, live_fallback=False
        )
        entities, relations = extractor.extract(
            [chunk], ontology, source_document_id="doc-alice"
        )

        assert len(entities) >= 1
        assert len(relations) >= 1
        for entity in entities:
            assert entity.entity_type in ontology.entity_types
            assert entity.extractor_id == "langextract"
        for relation in relations:
            assert relation.relation_type in ontology.relation_types
            assert relation.chunk_id == chunk.chunk_id

    def test_banned_entity_type_raises_ontology_violation(self) -> None:
        """Given a fixture with a banned entity type, extraction raises."""
        ontology = load_ontology("config/ontology.yaml")
        text = "This fixture is intentionally banned."
        chunk = Chunk(
            chunk_id="chunk-banned",
            document_id="doc-banned",
            text=text,
            source_type="text",
        )

        extractor = LangExtractExtractor(
            fixture_dir=_FIXTURE_DIR, live_fallback=False
        )
        with pytest.raises(OntologyViolationError) as exc_info:
            extractor.extract([chunk], ontology, source_document_id="doc-banned")

        assert exc_info.value.value == "BANNED_TYPE"
        assert exc_info.value.kind == "entity_type"

    def test_fixture_entity_offsets_are_strict(self) -> None:
        """Given recorded fixtures, entity offsets round-trip to chunk.text."""
        ontology = load_ontology("config/ontology.yaml")
        text = "Alice Smith works at Acme Corporation in New York."
        chunk = Chunk(
            chunk_id="chunk-offsets",
            document_id="doc-offsets",
            text=text,
            source_type="text",
        )

        extractor = LangExtractExtractor(
            fixture_dir=_FIXTURE_DIR, live_fallback=False
        )
        entities, _relations = extractor.extract(
            [chunk], ontology, source_document_id="doc-offsets"
        )

        assert len(entities) >= 1
        found = False
        for entity in entities:
            assert entity.chunk_id == chunk.chunk_id
            assert entity.source_start_char is not None
            assert entity.source_end_char is not None
            start = entity.source_start_char
            end = entity.source_end_char
            assert 0 <= start < end <= len(chunk.text)
            assert len(entity.name) > 0
            assert chunk.text[start:end] == entity.name
            found = True
        assert found

    def test_fixtures_are_input_hash_keyed_and_missing_raises(self) -> None:
        """Given distinct inputs, different fixtures load; missing fixture raises."""
        ontology = load_ontology("config/ontology.yaml")
        text_a = "Alice Smith works at Acme Corporation in New York."
        text_b = "Bob Johnson called Microsoft in Seattle."
        chunk_a = Chunk(
            chunk_id="chunk-a",
            document_id="doc-a",
            text=text_a,
            source_type="text",
        )
        chunk_b = Chunk(
            chunk_id="chunk-b",
            document_id="doc-b",
            text=text_b,
            source_type="text",
        )

        extractor = LangExtractExtractor(
            fixture_dir=_FIXTURE_DIR, live_fallback=False
        )
        entities_a, _ = extractor.extract(
            [chunk_a], ontology, source_document_id="doc-a"
        )
        entities_b, _ = extractor.extract(
            [chunk_b], ontology, source_document_id="doc-b"
        )

        names_a = {entity.name for entity in entities_a}
        names_b = {entity.name for entity in entities_b}
        assert names_a != names_b
        assert "Alice Smith" in names_a
        assert "Bob Johnson" in names_b

        missing_chunk = Chunk(
            chunk_id="chunk-missing",
            document_id="doc-missing",
            text="No fixture exists for this exact text.",
            source_type="text",
        )
        with pytest.raises(FileNotFoundError):
            extractor.extract(
                [missing_chunk], ontology, source_document_id="doc-missing"
            )

        assert (
            _FIXTURE_DIR / f"{_sha256(text_a)}.jsonl"
        ).exists()
        assert (
            _FIXTURE_DIR / f"{_sha256(text_b)}.jsonl"
        ).exists()

    def test_fixture_path_never_calls_live_extract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Given existing fixtures, langextract.extract is never invoked."""
        import langextract as lx

        mock = MagicMock()
        monkeypatch.setattr(lx, "extract", mock)

        ontology = load_ontology("config/ontology.yaml")
        text = "Alice Smith works at Acme Corporation in New York."
        chunk = Chunk(
            chunk_id="chunk-network",
            document_id="doc-network",
            text=text,
            source_type="text",
        )

        extractor = LangExtractExtractor(
            fixture_dir=_FIXTURE_DIR, live_fallback=False
        )
        entities, relations = extractor.extract(
            [chunk], ontology, source_document_id="doc-network"
        )

        assert len(entities) >= 1
        assert len(relations) >= 1
        assert mock.call_count == 0


class TestExtractorFactory:
    def test_factory_returns_regex_extractor(self) -> None:
        """Given config graph.extractor=regex, factory returns RegexExtractor."""
        extractor = create_extractor({"graph": {"extractor": "regex"}})
        assert isinstance(extractor, RegexExtractor)

    def test_factory_returns_langextract_extractor(self) -> None:
        """Given config graph.extractor=langextract, factory returns LangExtractExtractor."""
        extractor = create_extractor({"graph": {"extractor": "langextract"}})
        assert isinstance(extractor, LangExtractExtractor)

    def test_factory_defaults_to_langextract(self) -> None:
        """Given no explicit extractor, factory defaults to langextract."""
        extractor = create_extractor({"graph": {}})
        assert isinstance(extractor, LangExtractExtractor)

    def test_factory_llamaindex_raises_not_implemented(self) -> None:
        """Given config graph.extractor=llamaindex, factory raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            create_extractor({"graph": {"extractor": "llamaindex"}})
