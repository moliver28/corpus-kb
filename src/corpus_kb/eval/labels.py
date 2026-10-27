"""Label-to-chunk resolution (unified-ingestion Wave 7).

Golden records name units or spans; scorers need chunk ids. One module
owns every mapping rule so the calibration report can quote exactly one
rule string, and the metric math stays label-type agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass

from corpus_kb.eval.golden import GoldenRecord

__all__ = ["ChunkEntry", "LabelIndex", "overlap_rule", "resolve_relevance"]

DEFAULT_MIN_OVERLAP = 0.5


@dataclass(frozen=True)
class ChunkEntry:
    """One ingested chunk: its id, its doc, its char span, its unit ids."""

    chunk_id: str
    doc: str
    start: int
    end: int
    unit_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class LabelIndex:
    """Every chunk one calibration candidate ingested, plus the span rule."""

    entries: tuple[ChunkEntry, ...]
    min_overlap: float = DEFAULT_MIN_OVERLAP


def overlap_rule(min_overlap: float) -> str:
    """The human-readable span rule the report records verbatim."""
    return f"span overlap >= {min_overlap:.2f} of the chunk length"


def resolve_relevance(record: GoldenRecord, index: LabelIndex | None) -> frozenset[str]:
    """Resolve one record's labels to relevant chunk ids.

    - chunk-id labels pass through untouched (hand-copied ids).
    - unit labels map to the deterministic retrieval-chunk UUID the
      research path mints per unit. With an index, a unit no entry
      carries was never ingested and labels nothing.
    - span labels match chunks on the same doc whose covered fraction of
      the chunk reaches ``min_overlap``.
    An empty result is honest: the caller reports the query as skipped.
    """
    if record.relevant_chunk_ids:
        return frozenset(record.relevant_chunk_ids)
    if record.relevant_units:
        from corpus_kb.research.retrieval import _child_uuid

        units = list(record.relevant_units)
        if index is not None:
            known = {unit for entry in index.entries for unit in entry.unit_ids}
            units = [unit for unit in units if unit in known]
        return frozenset(_child_uuid(f"unit:{unit}") for unit in units)
    if record.relevant_spans:
        if index is None:
            return frozenset()
        return _span_matches(record, index)
    return frozenset()


def _span_matches(record: GoldenRecord, index: LabelIndex) -> frozenset[str]:
    hits: set[str] = set()
    for span in record.relevant_spans:
        for entry in index.entries:
            if entry.doc != span.doc:
                continue
            length = entry.end - entry.start
            if length <= 0:
                continue
            overlap = min(entry.end, span.end) - max(entry.start, span.start)
            if overlap <= 0:
                continue
            if overlap / length >= index.min_overlap:
                hits.add(entry.chunk_id)
    return frozenset(hits)
