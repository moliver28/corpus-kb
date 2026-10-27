"""Span and unit label resolution against an ingested label index."""

from __future__ import annotations

from corpus_kb.eval.golden import GoldenRecord, SpanLabel
from corpus_kb.eval.labels import (
    ChunkEntry,
    LabelIndex,
    overlap_rule,
    resolve_relevance,
)

_DOC = "sym://a"


def _index(min_overlap: float = 0.5) -> LabelIndex:
    return LabelIndex(
        entries=(
            ChunkEntry("c-half", _DOC, 0, 100),
            ChunkEntry("c-tail", _DOC, 100, 200),
            ChunkEntry("c-other", "sym://b", 0, 100),
        ),
        min_overlap=min_overlap,
    )


def _span(start: int, end: int, doc: str = _DOC) -> SpanLabel:
    return SpanLabel(doc=doc, start=start, end=end)


def test_chunk_id_labels_pass_through() -> None:
    record = GoldenRecord(query="q", relevant_chunk_ids=("c-half",))
    assert resolve_relevance(record, None) == {"c-half"}


def test_unit_labels_map_to_child_uuids() -> None:
    from corpus_kb.research.retrieval import _child_uuid

    record = GoldenRecord(query="q", relevant_chunk_ids=(), relevant_units=(5,))
    assert resolve_relevance(record, None) == {_child_uuid("unit:5")}


def test_span_labels_match_by_overlap_fraction() -> None:
    # The label covers 60 of c-half's 100 chars (0.60 >= 0.50): relevant.
    record = GoldenRecord(
        query="q",
        relevant_chunk_ids=(),
        relevant_spans=(_span(40, 140),),
    )
    hits = resolve_relevance(record, _index())
    assert "c-half" in hits
    assert "c-tail" not in hits
    assert "c-other" not in hits


def test_span_labels_below_fraction_do_not_match() -> None:
    # Only 40 of c-half's 100 chars covered (0.40 < 0.50): no match.
    record = GoldenRecord(
        query="q",
        relevant_chunk_ids=(),
        relevant_spans=(_span(60, 140),),
    )
    assert resolve_relevance(record, _index()) == set()


def test_strict_fraction_bound_honored() -> None:
    # 0.60 covered clears a 0.50 bar but not a 0.70 bar.
    record = GoldenRecord(
        query="q",
        relevant_chunk_ids=(),
        relevant_spans=(_span(40, 140),),
    )
    assert resolve_relevance(record, _index(min_overlap=0.7)) == set()


def test_rule_text_names_the_fraction() -> None:
    assert overlap_rule(0.5) == "span overlap >= 0.50 of the chunk length"
