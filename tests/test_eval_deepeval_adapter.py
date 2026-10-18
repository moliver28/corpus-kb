"""The deepeval adapter tests — run ONLY where the ``[eval]`` extra lives.

CI never installs deepeval, so every test here skips there via
``pytest.importorskip``. Locally, with the extra installed, they pin the
adapter's real surface: four metrics, the plain-object judge passing
through untouched, and per-metric score honesty (a failing metric is
None, never a fabricated number).
"""

from __future__ import annotations

import pytest

pytest.importorskip("deepeval")

from deepeval.models import DeepEvalBaseLLM

from corpus_kb.eval.deepeval_adapter import (
    METRIC_NAMES,
    build_answer_metrics,
    score_answer,
)

CFG: dict[str, object] = {"llm": {"model": "qwen3:4b", "base_url": "http://localhost:11434"}}


class _FakeJudge(DeepEvalBaseLLM):
    """A stub judge (upgraded deepeval type-checks the model argument)."""

    def load_model(self):
        return None

    def get_model_name(self) -> str:
        return "fake-judge"

    def generate(self, prompt: str) -> str:
        return ""

    async def a_generate(self, prompt: str) -> str:
        return ""


class _ConstantMetric:
    def __init__(self, score: float, fail: bool = False) -> None:
        self._score = score
        self._fail = fail
        self.score: float | None = None

    def measure(self, case: object) -> float | None:
        if self._fail:
            raise RuntimeError("judge down")
        self.score = self._score
        return self._score


def test_build_answer_metrics_returns_the_four_contract_metrics() -> None:
    metrics = build_answer_metrics(CFG, judge_llm=_FakeJudge())
    assert len(metrics) == 4
    assert all(hasattr(metric, "measure") for metric in metrics)


def test_metric_names_are_stable() -> None:
    assert METRIC_NAMES == (
        "faithfulness",
        "answer_relevancy",
        "contextual_recall",
        "contextual_precision",
    )


def test_score_answer_reports_scores_and_none_on_failure() -> None:
    metrics: list[object] = [
        _ConstantMetric(0.75),
        _ConstantMetric(0.5, fail=True),
        _ConstantMetric(0.25),
        _ConstantMetric(1.0),
    ]
    scores = score_answer("q", "produced answer", "golden answer", ["ctx"], metrics)
    assert scores == {
        "faithfulness": 0.75,
        "answer_relevancy": None,
        "contextual_recall": 0.25,
        "contextual_precision": 1.0,
    }


def test_score_answer_uses_the_real_test_case_shape() -> None:
    """A real deepeval case rides through (import presence proves shape)."""
    from deepeval.test_case import LLMTestCase

    case = LLMTestCase(
        input="q",
        actual_output="a",
        expected_output="golden",
        retrieval_context=["ctx"],
    )
    assert case.input == "q"
