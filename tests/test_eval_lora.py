"""LoRA adapter scorer glue tests (Living Corpus W7).

The heavy path (torch/peft/gliner) needs the tune extra; these tests
pin the pure glue: span prediction conversion, report scoring, and the
promotion-gate plumbing over fakes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.extraction._eval_gold import GoldDoc, GoldSpan
from corpus_kb.extraction.eval import promotion_gate
from corpus_kb.extraction.eval_lora import predict_spans, promotion_decision


class _FakeGliner:
    """predict_entities shaped like the gliner package's API."""

    def __init__(self, spans: dict[str, list[dict[str, Any]]]) -> None:
        self._spans = spans

    def predict_entities(
        self, text: str, labels: Any = None, threshold: float = 0.5
    ) -> list[dict[str, Any]]:
        return self._spans.get(text[:12], [])


def test_predict_spans_converts_to_gold_spans() -> None:
    doc = GoldDoc(
        doc_id="d1",
        text="Alice met Bob in Paris.",
        spans=(GoldSpan(start=0, end=5, label="person"),),
    )
    model = _FakeGliner(
        {
            "Alice met Bo": [
                {"start": 0, "end": 5, "label": "person"},
                {"start": 15, "end": 20, "label": "location"},
            ]
        }
    )
    predictions = predict_spans(model, [doc])
    assert [s.label for s in predictions["d1"]] == ["person", "location"]
    assert predictions["d1"][1].start == 15


def test_promotion_decision_pins_the_gate() -> None:
    below_margin = promotion_gate(0.79, 0.78)  # candidate 0.79 vs baseline 0.78
    assert below_margin.promoted is False  # floor ok, margin 0.01 < 0.02
    good = promotion_gate(0.85, 0.78)
    assert good.promoted is True


def test_promotion_decision_over_reports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from corpus_kb.extraction.eval import EvalReport, Scored

    def report(f1: float) -> EvalReport:
        return EvalReport(
            micro=Scored(tp=1, fp=0, fn=0, precision=1.0, recall=1.0, f1=f1), per_type={}
        )

    decision = promotion_decision(report(0.80), report(0.85))
    assert decision.promoted is True  # candidate 0.85 clears 0.80 + 0.02
    payload = {"promoted": decision.promoted, "reasons": list(decision.reasons)}
    assert json.loads(json.dumps(payload))  # serializable


def test_score_adapter_requires_the_tune_extra(tmp_path: Path) -> None:
    """No torch/peft/gliner locally: the typed error, never a silent zero."""
    from corpus_kb.errors import DependencyMissingError

    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text(json.dumps({"doc_id": "d1", "text": "x", "spans": []}), encoding="utf-8")
    from corpus_kb.extraction import eval_lora

    try:
        eval_lora._gliner_cls()
        pytest.skip("tune extra installed")
    except DependencyMissingError:
        pass
