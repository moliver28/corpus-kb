"""TREC-format export for calibration runs (moved out of eval/stats.py).

A qrels file from the golden labels plus one run file per candidate, so a
human can re-score every number on another machine with standard tools.
One shared ``query_id_map`` keeps the files aligned: the same query text
gets the SAME qid in both files, so a skipped query can never shift the
numbering apart.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import LabelIndex, resolve_relevance
from corpus_kb.eval.scoring import QueryScore

__all__ = ["query_id_map", "write_trec_qrels", "write_trec_run"]


def query_id_map(golden: Sequence[GoldenRecord]) -> dict[str, str]:
    """Map query text -> TREC qid (``q1``, ``q2``, ...), in golden order.

    One shared map keeps the qrels file and every run file aligned: the
    same query text gets the SAME qid in both files, so a skipped query
    can never shift the numbering apart.
    """
    return {record.query: f"q{position}" for position, record in enumerate(golden, start=1)}


def write_trec_qrels(
    path: Path,
    golden: Sequence[GoldenRecord],
    index: LabelIndex,
    min_overlap: float,
    *,
    qids: Mapping[str, str] | None = None,
) -> int:
    """TREC qrels from the golden labels: ``qid 0 docid gain`` per line.

    Labels resolve through the SAME rules the sweep scored with, so the
    exported file and the reported numbers can never disagree. Pass the
    ``query_id_map`` result as ``qids``; the default derives qids from the
    golden order. Returns the number of written lines.
    """
    qid_map = qids if qids is not None else query_id_map(golden)
    lines: list[str] = []
    for record in golden:
        relevant = sorted(resolve_relevance(record, LabelIndex(index.entries, min_overlap)))
        for doc_id in relevant:
            lines.append(f"{qid_map[record.query]} 0 {doc_id} 1")
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return len(lines)


def write_trec_run(
    path: Path,
    rows: list[QueryScore],
    *,
    qids: Mapping[str, str] | None = None,
) -> int:
    """TREC run for one candidate: ``qid Q0 docid rank score`` per line.

    Pass the SAME ``qids`` map the qrels file used (built by
    ``query_id_map`` over the golden records); the default derives qids
    from row order, which misaligns the files when a query was skipped.
    Returns the number of written lines.
    """
    qid_map = (
        qids
        if qids is not None
        else {row.query: f"q{position}" for position, row in enumerate(rows, start=1)}
    )
    lines: list[str] = []
    for row in rows:
        for rank, doc_id in enumerate(row.ranked, start=1):
            score = f"{1.0 / rank:.6f}"
            lines.append(f"{qid_map[row.query]} Q0 {doc_id} {rank} {score}")
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return len(lines)
