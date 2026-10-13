"""Tests for extraction/eval.py — scorer math on hand-computable inputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_kb.extraction.eval import (
    GoldDoc,
    GoldSpan,
    load_gold,
    paired_bootstrap,
    promotion_gate,
    score_corpus,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "ner_gold" / "gold.jsonl"


# ---------------------------------------------------------------------------
# Gold loader
# ---------------------------------------------------------------------------


def test_load_gold_shipped_fixture_is_wellformed() -> None:
    """The committed ner_gold fixture parses and its offsets round-trip."""
    docs = load_gold(_FIXTURE)
    assert [d.doc_id for d in docs] == ["gold-001", "gold-002", "gold-003"]
    first = docs[0]
    assert len(first.spans) == 5
    for span in first.spans:
        assert first.text[span.start : span.end]


def test_load_gold_rejects_out_of_range_offset(tmp_path: Path) -> None:
    """A span past the end of the text is a loader error, not silent."""
    path = tmp_path / "gold.jsonl"
    bad_span = {"start": 0, "end": 99, "label": "person"}
    path.write_text(
        json.dumps({"doc_id": "d", "text": "short", "spans": [bad_span]}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="out of range"):
        load_gold(path)


def test_load_gold_rejects_duplicate_doc_ids(tmp_path: Path) -> None:
    path = tmp_path / "gold.jsonl"
    line = json.dumps({"doc_id": "d", "text": "x", "spans": []})
    path.write_text(line + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate doc_id"):
        load_gold(path)


def test_load_gold_rejects_missing_text_or_nonint_offsets(tmp_path: Path) -> None:
    path = tmp_path / "gold.jsonl"
    path.write_text(json.dumps({"doc_id": "d", "spans": []}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="doc_id and text must be strings"):
        load_gold(path)

    path2 = tmp_path / "gold2.jsonl"
    path2.write_text(
        json.dumps(
            {"doc_id": "d", "text": "x", "spans": [{"start": True, "end": 1, "label": "person"}]}
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="start must be an int"):
        load_gold(path2)


# ---------------------------------------------------------------------------
# Scorer math (hand-computed)
# ---------------------------------------------------------------------------


def _hand_case() -> tuple[list[GoldDoc], dict, dict]:
    """gold: person(0,5)+org(10,14); candidate: person(0,5) tp,
    org(10,15) fp (wrong end), location(20,26) fp; org(10,14) fn."""
    docs = [
        GoldDoc(
            doc_id="d1",
            text="Alice met Acme today",
            spans=(
                GoldSpan(start=0, end=5, label="person"),
                GoldSpan(start=10, end=14, label="org"),
            ),
        )
    ]
    candidate = {
        "d1": [
            GoldSpan(start=0, end=5, label="person"),
            GoldSpan(start=10, end=15, label="org"),
            GoldSpan(start=16, end=20, label="location"),
        ]
    }
    baseline: dict[str, list[GoldSpan]] = {}
    return docs, candidate, baseline


def test_score_corpus_micro_is_exact() -> None:
    """tp=1 fp=2 fn=1 -> P=1/3 R=1/2 F1=0.4 exactly."""
    docs, candidate, _ = _hand_case()
    report = score_corpus(docs, candidate)
    assert report.micro.tp == 1
    assert report.micro.fp == 2
    assert report.micro.fn == 1
    assert report.micro.precision == pytest.approx(1 / 3)
    assert report.micro.recall == pytest.approx(0.5)
    assert report.micro.f1 == pytest.approx(0.4)


def test_score_corpus_per_type_is_exact() -> None:
    """person perfect; org 0/0 after miss+fp -> 0; location pure fp -> 0."""
    docs, candidate, _ = _hand_case()
    report = score_corpus(docs, candidate)
    assert report.per_type["person"].f1 == pytest.approx(1.0)
    assert report.per_type["org"].tp == 0
    assert report.per_type["org"].f1 == 0.0
    assert report.per_type["location"].precision == 0.0
    assert report.per_type["location"].recall == 0.0


def test_score_corpus_empty_predictions_zero_not_nan() -> None:
    docs, _, baseline = _hand_case()
    report = score_corpus(docs, baseline)
    assert report.micro.f1 == 0.0
    assert report.micro.precision == 0.0


# ---------------------------------------------------------------------------
# Paired bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_perfect_vs_empty_has_degenerate_ci_at_delta() -> None:
    """Candidate perfect on every doc, baseline empty: every resample has
    delta 1.0, so the 95% CI is degenerate at exactly [1.0, 1.0]."""
    gold_docs = load_gold(_FIXTURE)
    candidate = {
        doc.doc_id: [GoldSpan(s.start, s.end, s.label) for s in doc.spans] for doc in gold_docs
    }
    result = paired_bootstrap(gold_docs, candidate, {}, resamples=200, seed=7)
    assert result.delta_f1 == pytest.approx(1.0)
    assert result.ci_low == pytest.approx(1.0)
    assert result.ci_high == pytest.approx(1.0)


def test_bootstrap_is_seeded_and_reproducible() -> None:
    """Same seed reproduces bit-identical CIs."""
    gold_docs = load_gold(_FIXTURE)
    candidate = {
        doc.doc_id: [GoldSpan(s.start, s.end, s.label) for s in doc.spans] for doc in gold_docs
    }
    # Perturb one span so deltas are not degenerate.
    candidate["gold-001"][0] = GoldSpan(start=0, end=10, label="person")
    a = paired_bootstrap(gold_docs, candidate, {}, resamples=500, seed=42)
    b = paired_bootstrap(gold_docs, candidate, {}, resamples=500, seed=42)
    assert (a.ci_low, a.ci_high) == (b.ci_low, b.ci_high)
    assert a.ci_low <= a.delta_f1 <= a.ci_high


def test_bootstrap_matches_point_delta() -> None:
    """point delta_f1 equals the micro F1 difference from score_corpus."""
    gold_docs = load_gold(_FIXTURE)
    candidate = {
        doc.doc_id: [GoldSpan(s.start, s.end, s.label) for s in doc.spans] for doc in gold_docs
    }
    candidate["gold-001"] = candidate["gold-001"][:2]
    baseline = {
        "gold-001": [GoldSpan(s.start, s.end, s.label) for s in gold_docs[0].spans[:1]],
    }
    result = paired_bootstrap(gold_docs, candidate, baseline, resamples=100, seed=3)
    cand_f1 = score_corpus(gold_docs, candidate).micro.f1
    base_f1 = score_corpus(gold_docs, baseline).micro.f1
    assert result.delta_f1 == pytest.approx(cand_f1 - base_f1)


# ---------------------------------------------------------------------------
# Promotion gate
# ---------------------------------------------------------------------------


def test_promotion_gate_fails_below_absolute_floor() -> None:
    decision = promotion_gate(candidate_f1=0.4, baseline_f1=0.0)
    assert decision.promoted is False
    assert any(r.startswith("FAIL absolute") for r in decision.reasons)
    assert any(r.startswith("PASS relative") for r in decision.reasons)


def test_promotion_gate_fails_without_margin_over_baseline() -> None:
    decision = promotion_gate(candidate_f1=0.81, baseline_f1=0.80)
    assert decision.promoted is False
    assert any(r.startswith("FAIL relative") for r in decision.reasons)


def test_promotion_gate_passes_at_floor_plus_margin() -> None:
    assert promotion_gate(candidate_f1=0.90, baseline_f1=0.85).promoted is True
    # Boundary: exactly floor and exactly baseline+margin passes.
    assert promotion_gate(candidate_f1=0.82, baseline_f1=0.80).promoted is True
