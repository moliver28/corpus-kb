"""Paired statistics, TREC export, and the report (Wave 7 Phase 4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.eval._ranx import ranx_available
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import ChunkEntry, LabelIndex
from corpus_kb.eval.scoring import QueryScore
from corpus_kb.eval.stats import compare_all, paired_comparison, write_report
from corpus_kb.eval.trec import query_id_map, write_trec_qrels, write_trec_run
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


# ============================================================================
# compare_all: every challenger vs the baseline, Holm-corrected
# ============================================================================


def _family() -> dict[str, list[QueryScore]]:
    return {
        "baseline": _rows("baseline", [0.0, 0.0, 0.0]),
        "alpha": _rows("alpha", [1.0, 1.0, 1.0]),
        "beta": _rows("beta", [1.0, 1.0, 1.0]),
    }


def _p_by_challenger(table: dict[str, float]):
    def fake_p(
        query_scores: object, baseline: str, challenger: str, metric: str, k: int | None
    ) -> float | None:
        return table.get(challenger)

    return fake_p


def test_compare_all_marks_significant_winners(monkeypatch) -> None:
    monkeypatch.setattr("corpus_kb.eval.stats._ranx_p_value", _p_by_challenger({"alpha": 0.01}))
    outcomes = compare_all(_family(), "baseline", metric="recall", alpha=0.05)
    assert [outcome.challenger for outcome in outcomes] == ["alpha", "beta"]
    by_name = {outcome.challenger: outcome for outcome in outcomes}
    assert by_name["alpha"].significant is True
    assert by_name["beta"].significant is False
    assert by_name["alpha"].delta > 0


def test_holm_step_down_stops_at_the_first_failure(monkeypatch) -> None:
    # Holm: p=[0.03, 0.04], m=2. The smallest scales to 0.06 > 0.05 and
    # fails, so the 0.04 p-value is NOT significant despite p < alpha.
    monkeypatch.setattr(
        "corpus_kb.eval.stats._ranx_p_value", _p_by_challenger({"alpha": 0.03, "beta": 0.04})
    )
    outcomes = compare_all(_family(), "baseline", metric="recall", alpha=0.05)
    by_name = {outcome.challenger: outcome for outcome in outcomes}
    assert by_name["alpha"].significant is False
    assert by_name["beta"].significant is False


def test_holm_step_down_keeps_both_when_scaling_holds(monkeypatch) -> None:
    # p=[0.02, 0.03]: 0.02*2 = 0.04 passes, then 0.03*1 = 0.03 passes.
    monkeypatch.setattr(
        "corpus_kb.eval.stats._ranx_p_value", _p_by_challenger({"alpha": 0.02, "beta": 0.03})
    )
    outcomes = compare_all(_family(), "baseline", metric="recall", alpha=0.05)
    assert all(outcome.significant for outcome in outcomes)


def test_compare_all_requires_the_baseline(monkeypatch) -> None:
    monkeypatch.setattr("corpus_kb.eval.stats._ranx_p_value", _p_by_challenger({}))
    with pytest.raises(ValidationError, match="baseline"):
        compare_all({"solo": _rows("solo", [1.0])}, "absent", metric="recall")


def test_compare_all_sign_test_marks_nothing_significant(monkeypatch) -> None:
    monkeypatch.setattr("corpus_kb.eval._ranx.ranx_available", lambda: False)
    outcomes = compare_all(_family(), "baseline", metric="recall")
    assert all(outcome.p_value is None for outcome in outcomes)
    assert all(outcome.significant is False for outcome in outcomes)
    assert all(outcome.method.startswith("sign test") for outcome in outcomes)
