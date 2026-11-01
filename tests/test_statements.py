"""Tests for statement atoms (Living Corpus W2): template catalogue,
atomization, row shape, and write-once storage."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.statements import (
    StatementStore,
    TemplateCatalogue,
    atomize_chunk,
    load_templates,
    statement_row,
    template_histogram,
)
from corpus_kb.statements.atomize import MAX_STATEMENTS_PER_CHUNK
from corpus_kb.utils.models import Chunk, Entity

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "config" / "claim_templates"


@pytest.fixture(scope="module")
def catalogue() -> TemplateCatalogue:
    return load_templates(TEMPLATES_DIR)


def _chunk(text: str, **meta: Any) -> Chunk:
    return Chunk(
        document_id="doc-1",
        text=text,
        source_type="text",
        metadata=meta or {},
        chunk_id="chunk-1",
    )


def _entity(name: str, start: int, end: int, confidence: float = 0.9) -> Entity:
    return Entity(
        name=name,
        entity_type="PERSON",
        source_type="text",
        source_document_id="doc-1",
        chunk_id="chunk-1",
        source_start_char=start,
        source_end_char=end,
        confidence=confidence,
    )


def test_catalogue_loads_shipped_templates_with_fallback(catalogue: TemplateCatalogue) -> None:
    ids = catalogue.ids()
    for expected in (
        "preference",
        "opinion",
        "sentiment_target",
        "fact_triple",
        "numeric_claim",
        "temporal_change",
        "attribution",
        "statement",
    ):
        assert expected in ids


def test_classify_preference_and_temporal_change(catalogue: TemplateCatalogue) -> None:
    assert catalogue.classify("I really like ice cream after dinner.") == "preference"
    assert catalogue.classify("I used to like pistachio.") == "temporal_change"
    assert catalogue.classify("The quarterly numbers were terrible.") == "sentiment_target"
    assert catalogue.classify("A quiet sentence about nothing.") == "statement"


def test_catalogue_rejects_duplicates_and_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        load_templates(tmp_path)
    from corpus_kb.statements.templates import TemplateSpec

    spec = TemplateSpec(id="x", description="dup")
    with pytest.raises(ValueError, match="duplicate"):
        TemplateCatalogue([spec, spec.model_copy()])


def test_atomize_chunk_classifies_and_anchors(catalogue: TemplateCatalogue) -> None:
    text = "I really like ice cream. I used to like pistachio years ago."
    chunk = _chunk(text)
    entities = [_entity("ice cream", 13, 22), _entity("pistachio", 40, 49)]
    atoms = atomize_chunk(chunk, entities, catalogue)

    assert [a.template_id for a in atoms] == ["preference", "temporal_change"]
    assert atoms[0].exchange_id is None
    assert [e.name for e in atoms[0].entities] == ["ice cream"]
    # Deterministic: same input, same hashes.
    again = atomize_chunk(chunk, entities, catalogue)
    assert [a.statement_hash for a in again] == [a.statement_hash for a in atoms]


def test_atomize_chunk_carries_the_qa_exchange(catalogue: TemplateCatalogue) -> None:
    chunk = _chunk("I prefer decaf now.", qa_context={"exchange_id": 42})
    atoms = atomize_chunk(chunk, [_entity("decaf", 10, 15)], catalogue)
    assert all(a.exchange_id == 42 for a in atoms)


def test_atomize_chunk_respects_min_entities(catalogue: TemplateCatalogue) -> None:
    text = "She works at the hospital near the river."
    chunk = _chunk(text)
    atoms = atomize_chunk(chunk, [], catalogue)  # fact_triple needs 2 entities
    assert all(a.template_id != "fact_triple" for a in atoms)


def _many_sentence_chunk(count: int) -> Chunk:
    text = " ".join(
        f"The review board published minutes for session {n} today." for n in range(count)
    )
    return _chunk(text)


def test_atomize_chunk_default_cap_is_twelve(catalogue: TemplateCatalogue) -> None:
    """Without the knob the shipped cap is 12 atoms, identical to today."""
    assert MAX_STATEMENTS_PER_CHUNK == 12
    atoms = atomize_chunk(_many_sentence_chunk(16), [], catalogue)
    assert len(atoms) == 12


def test_atomize_chunk_honors_max_per_chunk(catalogue: TemplateCatalogue) -> None:
    """A lower configured cap drops the overflow atoms deterministically."""
    chunk = _many_sentence_chunk(16)
    assert len(atomize_chunk(chunk, [], catalogue, max_per_chunk=3)) == 3
    assert len(atomize_chunk(chunk, [], catalogue, max_per_chunk=1)) == 1


def test_stage_max_per_chunk_reader() -> None:
    """The pipeline stage reads the knob; anything unreadable keeps 12.

    The reader lives in tools/pipeline/atomize.py (the one config seam);
    the validator pins int >= 1 when the key is present.
    """
    from corpus_kb.tools.pipeline.atomize import _stage_max_per_chunk

    assert _stage_max_per_chunk({}) == 12
    assert _stage_max_per_chunk({"max_per_chunk": 5}) == 5
    assert _stage_max_per_chunk({"max_per_chunk": True}) == 12
    assert _stage_max_per_chunk({"max_per_chunk": 0}) == 12
    assert _stage_max_per_chunk({"max_per_chunk": "3"}) == 12


def test_template_histogram_is_sorted() -> None:
    from corpus_kb.statements.atomize import StatementAtom

    def atom(template_id: str) -> StatementAtom:
        return StatementAtom(
            chunk_id="c",
            document_id="d",
            text="t",
            template_id=template_id,
            start_char=0,
            end_char=1,
            entities=[],
            exchange_id=None,
        )

    assert template_histogram([atom("b"), atom("a"), atom("b")]) == {"a": 1, "b": 2}


def test_statement_row_shape(catalogue: TemplateCatalogue) -> None:
    text = "I really like ice cream."
    chunk = _chunk(text)
    atom = atomize_chunk(chunk, [_entity("ice cream", 13, 22)], catalogue)[0]
    row = statement_row(atom, "tenant-1", model_version="mv", prompt_version="pv")
    assert len(row) == 15
    assert row[0] == "tenant-1"
    assert row[4] == text
    assert "mv" in row[14]


class _FakeTx:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def __getattr__(self, name: str) -> Callable[..., object]:
        if name == "execute":
            return self._run
        if name == "fetchval":
            return self._insert
        raise AttributeError(name)

    async def _run(self, *args: object) -> str:
        self._store.setdefault("inserts", []).append(args)
        return "INSERT 0 1"

    async def _insert(self, *args: object) -> str:
        self._store.setdefault("inserts", []).append(args)
        return f"insert-{len(self._store['inserts'])}"

    async def __aenter__(self) -> _FakeTx:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeGateway:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def transaction(self, tenant_id: str) -> _FakeTx:
        self.store["tenant"] = tenant_id
        return _FakeTx(self.store)


@pytest.mark.asyncio
async def test_store_writes_one_row_per_atom(catalogue: TemplateCatalogue) -> None:
    gateway = _FakeGateway()
    store = StatementStore(gateway, "tenant-1")
    chunk = _chunk("I really like ice cream.")
    atoms = atomize_chunk(chunk, [_entity("ice cream", 13, 22)], catalogue)
    written = await store.store_statements(atoms, model_version="mv")
    assert written == len(atoms)
    assert len(gateway.store["inserts"]) == len(atoms)
    assert gateway.store["tenant"] == "tenant-1"


@pytest.mark.asyncio
async def test_store_zero_atoms_is_noop() -> None:
    store = StatementStore(_FakeGateway(), "tenant-1")
    assert await store.store_statements([]) == 0
