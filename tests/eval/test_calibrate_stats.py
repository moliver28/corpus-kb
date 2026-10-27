"""Paired statistics, TREC export, and the report (Wave 7 Phase 4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.eval._ranx import ranx_available
from corpus_kb.eval.calibrate import QueryScore
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import ChunkEntry, LabelIndex
from corpus_kb.eval.stats import (
    paired_comparison,
    query_id_map,
    write_report,
    write_trec_qrels,
    write_trec_run,
)
from corpus_kb.research.retrieval import _child_uuid


def _rows(candidate: str, values: list[float]) -> list[QueryScore]:
    return [
        QueryScore(
            candidate=candidate,
            query=f"q{index}",
            recall_at_k=value,
            mrr=value,
            ndcg_at_10=value,
            relevant=frozenset({"d-hit"}),
            ranked=("d-hit", "d-miss") if value else ("d-miss", "d-hit"),
        )
        for index, value in enumerate(values, start=1)
    ]


def _scores() -> dict[str, list[QueryScore]]:
    return {
        "baseline": _rows("baseline", [1.0, 0.0, 0.5]),
        "challenger": _rows("challenger", [0.0, 1.0, 0.5]),
    }


def test_sign_test_counts_wins_losses_and_ties(monkeypatch) -> None:
    monkeypatch.setattr("corpus_kb.eval._ranx.ranx_available", lambda: False)
    outcome = paired_comparison(_scores(), "baseline", "challenger", metric="recall")
    assert outcome.method.startswith("sign test")
    assert outcome.p_value is None
    assert (outcome.wins, outcome.losses, outcome.ties) == (1, 1, 1)
    assert outcome.delta == 0.0


def test_delta_is_challenger_minus_baseline(monkeypatch) -> None:
    monkeypatch.setattr("corpus_kb.eval._ranx.ranx_available", lambda: False)
    scores = _scores()
    scores["challenger"] = _rows("challenger", [1.0, 1.0, 1.0])
    outcome = paired_comparison(scores, "baseline", "challenger", metric="ndcg")
    assert outcome.delta > 0
    assert outcome.wins == 2 and outcome.losses == 0


def test_paired_by_query_text_not_position(monkeypatch) -> None:
    monkeypatch.setattr("corpus_kb.eval._ranx.ranx_available", lambda: False)
    base = _rows("baseline", [0.0, 1.0, 0.5])
    # Same per-query values as baseline's but listed in a different order.
    challenge = [
        QueryScore(
            candidate="challenger",
            query=row.query,
            recall_at_k=row.recall_at_k,
            mrr=row.mrr,
            ndcg_at_10=row.ndcg_at_10,
            relevant=row.relevant,
            ranked=row.ranked,
        )
        for row in reversed(base)
    ]
    scores = {"baseline": base, "challenger": challenge}
    outcome = paired_comparison(scores, "baseline", "challenger", metric="recall")
    assert (outcome.wins, outcome.losses, outcome.ties) == (0, 0, 3)
    assert outcome.delta == 0.0


def test_different_query_sets_refused(monkeypatch) -> None:
    monkeypatch.setattr("corpus_kb.eval._ranx.ranx_available", lambda: False)
    scores = _scores()
    scores["challenger"] = _rows("challenger", [1.0, 1.0])  # q1 and q2 only
    with pytest.raises(ValidationError, match="different query sets"):
        paired_comparison(scores, "baseline", "challenger", metric="recall")


def test_trec_qrels_use_resolved_labels(tmp_path: Path) -> None:
    from corpus_kb.eval.golden import SpanLabel

    entry = ChunkEntry(_child_uuid("unit:4"), "sym://a", 0, 100, unit_ids=(4,))
    record = GoldenRecord(
        query="q",
        relevant_chunk_ids=(),
        relevant_spans=(SpanLabel(doc="sym://a", start=0, end=50),),
    )
    path = tmp_path / "qrels.txt"
    written = write_trec_qrels(path, [record], LabelIndex((entry,)), 0.5)
    assert written == 1
    assert path.read_text(encoding="utf-8") == f"q1 0 {_child_uuid('unit:4')} 1\n"


def test_trec_run_ranks_in_order(tmp_path: Path) -> None:
    path = tmp_path / "run.txt"
    written = write_trec_run(path, _rows("baseline", [1.0]))
    assert written == 2
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].split()[0:4] == ["q1", "Q0", "d-hit", "1"]


def test_shared_qid_map_aligns_qrels_and_run(tmp_path: Path) -> None:
    from corpus_kb.eval.golden import SpanLabel

    span = SpanLabel(doc="sym://a", start=0, end=50)
    golden = [
        GoldenRecord(query="first", relevant_chunk_ids=(), relevant_spans=(span,)),
        GoldenRecord(query="second", relevant_chunk_ids=(), relevant_spans=(span,)),
    ]
    entry = ChunkEntry(_child_uuid("unit:4"), "sym://a", 0, 100, unit_ids=(4,))
    qids = query_id_map(golden)
    # The candidate skipped the first query, so only the second query's rows exist.
    rows = [
        QueryScore(
            candidate="baseline",
            query="second",
            recall_at_k=1.0,
            mrr=1.0,
            ndcg_at_10=1.0,
            relevant=frozenset({_child_uuid("unit:4")}),
            ranked=(_child_uuid("unit:4"),),
        )
    ]
    qrels_path = tmp_path / "qrels.txt"
    run_path = tmp_path / "run.txt"
    write_trec_qrels(qrels_path, golden, LabelIndex((entry,)), 0.5, qids=qids)
    write_trec_run(run_path, rows, qids=qids)
    qrels_lines = qrels_path.read_text(encoding="utf-8").splitlines()
    run_lines = run_path.read_text(encoding="utf-8").splitlines()
    assert f"q2 0 {_child_uuid('unit:4')} 1" in qrels_lines
    assert run_lines[0].split()[0:3] == ["q2", "Q0", _child_uuid("unit:4")]


def test_report_merges_summary_and_outcomes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("corpus_kb.eval._ranx.ranx_available", lambda: False)
    outcome = paired_comparison(_scores(), "baseline", "challenger", metric="recall")
    path = tmp_path / "report.json"
    write_report(path, summary={"chosen": "challenger"}, outcomes=[outcome])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["calibration"] == {"chosen": "challenger"}
    assert payload["paired"][0]["challenger"] == "challenger"
    assert payload["paired"][0]["method"].startswith("sign test")


def test_fisher_outcome_when_ranx_installed() -> None:
    if not ranx_available():
        return  # CI never installs the calibrate extra; the sign test covers it
    outcome = paired_comparison(_scores(), "baseline", "challenger", metric="recall", k=2)
    assert outcome.method.startswith("fisher randomization")
    assert isinstance(outcome.p_value, float)
    assert 0.0 <= outcome.p_value <= 1.0
