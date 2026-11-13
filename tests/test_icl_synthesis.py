"""Tests for self-prompting ICL: examples synthesized from the ontology."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from corpus_kb.extraction._langextract_types import build_prompt_description
from corpus_kb.extraction.icl_synthesis import (
    _CACHE,
    _adopt_batch,
    few_shot_examples,
    ontology_fingerprint,
    static_exemplar_dicts,
    synthesize_exemplars,
)
from corpus_kb.extraction.icl_validate import exemplar_defects
from corpus_kb.extraction.langextract_backend import LangExtractExtractor
from corpus_kb.ontology import Ontology, load_ontology
from corpus_kb.utils.models import Chunk

_ROOT = Path(__file__).resolve().parent.parent
_ONTOLOGY_PATH = _ROOT / "src" / "corpus_kb" / "config" / "ontology.yaml"
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "langextract_recorded"


def _shipped_ontology() -> Ontology:
    return load_ontology(_ONTOLOGY_PATH)


def _small_ontology() -> Ontology:
    return Ontology(
        entity_types=["Document", "Person", "Concept"],
        relation_types=["AUTHORED_BY", "RELATED_TO"],
    )


@pytest.fixture(autouse=True)
def _isolated_cache() -> None:
    _CACHE.clear()
    yield
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


class TestOntologyFingerprint:
    def test_same_content_gives_same_fingerprint(self) -> None:
        """Two loads of the same ontology file hash to the same value."""
        assert ontology_fingerprint(_shipped_ontology()) == ontology_fingerprint(
            _shipped_ontology()
        )

    def test_changed_relations_change_fingerprint(self) -> None:
        """Adding one relation type changes the hash."""
        base = _small_ontology()
        grown = Ontology(
            entity_types=base.entity_types,
            relation_types=[*base.relation_types, "CITES"],
        )
        assert ontology_fingerprint(base) != ontology_fingerprint(grown)


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


class TestSynthesizeExemplars:
    def test_one_exemplar_per_relation_type(self) -> None:
        """Every relation type in the shipped ontology gets one exemplar."""
        ontology = _shipped_ontology()
        batch = synthesize_exemplars(ontology)
        assert len(batch) == len(ontology.relation_types)
        seen = [e["extractions"][-1]["extraction_class"] for e in batch]
        assert seen == ontology.relation_types
        assert exemplar_defects(batch, ontology) == []

    def test_entity_offsets_round_trip(self) -> None:
        """Recorded entity spans point at their own mention text."""
        for exemplar in synthesize_exemplars(_shipped_ontology()):
            text = exemplar["text"]
            assert isinstance(text, str)
            for item in exemplar["extractions"][:-1]:
                start, end = item["start_pos"], item["end_pos"]
                assert text[start:end] == item["extraction_text"]

    def test_relation_attributes_name_both_endpoints(self) -> None:
        """The relation extraction names subject and object mentions."""
        for exemplar in synthesize_exemplars(_small_ontology()):
            relation = exemplar["extractions"][-1]
            attrs = relation["attributes"]
            mentions = {item["extraction_text"] for item in exemplar["extractions"][:-1]}
            assert attrs["subject"] in mentions
            assert attrs["object"] in mentions
            assert 0.0 <= attrs["confidence"] <= 1.0

    def test_new_relation_type_gets_an_exemplar(self) -> None:
        """An ontology that grows a relation type gets an example for it."""
        base = _small_ontology()
        grown = Ontology(
            entity_types=base.entity_types,
            relation_types=[*base.relation_types, "CALLS"],
        )
        seen = [e["extractions"][-1]["extraction_class"] for e in synthesize_exemplars(grown)]
        assert "CALLS" in seen


class TestPromptDeterminism:
    def test_same_ontology_yields_byte_identical_prompt(self) -> None:
        """The prompt bytes depend only on ontology content."""
        rendered = []
        for _ in range(2):
            ontology = _shipped_ontology()
            batch = synthesize_exemplars(ontology)
            prompt = build_prompt_description(ontology) + json.dumps(
                batch, sort_keys=True, separators=(",", ":")
            )
            rendered.append(hashlib.sha256(prompt.encode("utf-8")).hexdigest())
        assert rendered[0] == rendered[1]

    def test_different_ontology_yields_different_prompt(self) -> None:
        """A grown ontology produces different prompt bytes."""
        base = _small_ontology()
        grown = Ontology(
            entity_types=base.entity_types,
            relation_types=[*base.relation_types, "CITES"],
        )
        a = json.dumps(synthesize_exemplars(base), sort_keys=True)
        b = json.dumps(synthesize_exemplars(grown), sort_keys=True)
        assert a != b


# ---------------------------------------------------------------------------
# Degenerate-synthesis rejection and static fallback
# ---------------------------------------------------------------------------


class TestExemplarDefects:
    def test_empty_batch_is_rejected(self) -> None:
        assert exemplar_defects([], _small_ontology()) != []

    def test_duplicated_text_is_rejected(self) -> None:
        batch = synthesize_exemplars(_small_ontology())
        duplicated = (batch[0], batch[0])
        assert "duplicate" in " ".join(exemplar_defects(duplicated, _small_ontology()))

    def test_missing_relation_signature_is_rejected(self) -> None:
        """A relation extraction without attributes is a defect."""
        exemplar = synthesize_exemplars(_small_ontology())[0]
        relation = dict(exemplar["extractions"][-1])
        relation["attributes"] = {}
        broken = (
            {
                "text": exemplar["text"],
                "extractions": [*exemplar["extractions"][:-1], relation],
            },
        )
        defects = exemplar_defects(broken, _small_ontology())
        assert defects != []

    def test_bad_offsets_are_rejected(self) -> None:
        exemplar = synthesize_exemplars(_small_ontology())[0]
        first = dict(exemplar["extractions"][0])
        first["start_pos"] = 0
        first["end_pos"] = len(exemplar["text"]) + 5
        broken = ({"text": exemplar["text"], "extractions": [first, *exemplar["extractions"][1:]]},)
        assert exemplar_defects(broken, _small_ontology()) != []

    def test_missing_relation_coverage_is_rejected(self) -> None:
        """A batch that skips a declared relation type is a defect."""
        batch = synthesize_exemplars(_small_ontology())[:1]
        assert exemplar_defects(tuple(batch), _small_ontology()) != []


class TestStaticFallback:
    def test_blank_description_uses_relation_name(self) -> None:
        """A blank relation description degrades to the relation name."""
        ontology = Ontology(
            entity_types=["Document", "Person"],
            relation_types=["AUTHORED_BY"],
            relation_descriptions={"AUTHORED_BY": "   "},
        )
        batch, note = few_shot_examples(ontology)
        assert "built" in note
        assert batch[0]["extractions"][-1]["extraction_text"] == "authored by"

    def test_degenerate_batch_falls_back_to_static(self) -> None:
        """A rejected synthesis batch is replaced by the static set."""
        ontology = _small_ontology()
        batch, note = _adopt_batch((), ontology, "deadbeef")
        assert "static" in note
        assert batch[0]["text"].startswith("Example of ")
        assert exemplar_defects(batch, ontology) != []

    def test_static_fallback_shape_is_stable(self) -> None:
        """The static fallback dict set keeps the legacy example shape."""
        ontology = _small_ontology()
        batch = static_exemplar_dicts(ontology)
        assert batch[0]["text"] == "Example of Document."
        assert batch[0]["extractions"][0]["extraction_class"] == "Document"
        assert batch[0]["extractions"][0]["extraction_text"] == "Document"
        assert batch[-1]["extractions"][-1]["extraction_class"] == "AUTHORED_BY"


# ---------------------------------------------------------------------------
# Cache and honest receipt
# ---------------------------------------------------------------------------


class TestFewShotCache:
    def test_first_call_reports_synthesized(self) -> None:
        _batch, note = few_shot_examples(_small_ontology())
        assert "built" in note
        assert "static" not in note

    def test_second_call_reports_reused(self) -> None:
        ontology = _small_ontology()
        first, note_a = few_shot_examples(ontology)
        second, note_b = few_shot_examples(ontology)
        assert first == second
        assert "reused" in note_b
        assert "built" in note_a

    def test_new_ontology_regenerates(self) -> None:
        few_shot_examples(_small_ontology())
        _batch, note = few_shot_examples(_shipped_ontology())
        assert "reused" not in note

    def test_cache_is_bounded(self) -> None:
        for i in range(12):
            few_shot_examples(
                Ontology(
                    entity_types=["Document", "Person"],
                    relation_types=[f"REL_{i}"],
                )
            )
        _batch, note = few_shot_examples(
            Ontology(entity_types=["Document", "Person"], relation_types=["REL_0"])
        )
        # The first fingerprint was evicted, so this is a fresh synthesis.
        assert "reused" not in note


# ---------------------------------------------------------------------------
# Backend wiring
# ---------------------------------------------------------------------------


class _CapturingLX:
    """A stub langextract module that records the live-call arguments."""

    def __init__(self) -> None:
        self.captured: list[dict[str, Any]] = []

    class data:  # noqa: N801 - mirrors the langextract module shape
        @staticmethod
        def CharInterval(**kwargs: Any) -> dict[str, Any]:  # noqa: N802
            return kwargs

        @staticmethod
        def Extraction(**kwargs: Any) -> dict[str, Any]:  # noqa: N802
            return kwargs

        @staticmethod
        def ExampleData(**kwargs: Any) -> dict[str, Any]:  # noqa: N802
            return kwargs

    def extract(self, **kwargs: Any) -> Any:
        self.captured.append(kwargs)
        return SimpleNamespace(extractions=[])


class TestBackendReceipt:
    def test_live_path_records_synthesis_receipt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A live extraction run records the synthesis receipt in stats."""
        stub = _CapturingLX()
        monkeypatch.setattr(
            "corpus_kb.extraction.langextract_backend.import_langextract", lambda: stub
        )
        extractor = LangExtractExtractor(live_fallback=True)
        chunk = Chunk(chunk_id="c1", document_id="d1", text="Doc body.", source_type="text")
        _entities, _relations, stats = extractor.extract(
            [chunk], _shipped_ontology(), source_document_id="d1"
        )
        assert any("Few-shot examples: built" in note for note in stats.notes)
        prompt = stub.captured[0]["prompt_description"]
        assert prompt == build_prompt_description(_shipped_ontology())

    def test_second_run_reports_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _CapturingLX()
        monkeypatch.setattr(
            "corpus_kb.extraction.langextract_backend.import_langextract", lambda: stub
        )
        extractor = LangExtractExtractor(live_fallback=True)
        ontology = _shipped_ontology()
        chunk = Chunk(chunk_id="c1", document_id="d1", text="Doc body.", source_type="text")
        extractor.extract([chunk], ontology, source_document_id="d1")
        _e, _r, stats = extractor.extract([chunk], ontology, source_document_id="d1")
        assert any("reused" in note for note in stats.notes)

    def test_fixture_path_builds_no_examples_and_no_note(self) -> None:
        """Recorded fixtures need no synthesized examples; no receipt note."""
        extractor = LangExtractExtractor(fixture_dir=_FIXTURE_DIR, live_fallback=False)
        ontology = _shipped_ontology()
        chunk = Chunk(
            chunk_id="c1",
            document_id="d1",
            text="Alice Smith works at Acme Corporation in New York.",
            source_type="text",
        )
        _entities, _relations, stats = extractor.extract([chunk], ontology, source_document_id="d1")
        assert stats.notes == ()
