"""The ONLY module that imports deepeval (lazily, behind the seam).

The ``[eval]`` extra is optional and CI never installs it, so deepeval
loads exactly like ``topics/engine.py`` loads bertopic: an
``_import_module`` seam tests can substitute, Protocols pinning the
small surface we use, and a typed ``EvalDependencyError`` carrying the
one-line remedy.

The local-judge guarantee lives here too: the LLM deepeval's metrics
talk to is an ``ollama.Client`` chat wrapper at temperature 0 pointed at
the local Ollama base URL. No API key exists on this path; no cloud
provider is reachable from it. Because ``DeepEvalBaseLLM`` must be
subclassed (the real class is required at class-creation time), the
subclass is built INSIDE a factory function after the lazy import — the
same construction style ``topics/engine.py`` uses for its model object.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Protocol, cast

from ollama import Client

from corpus_kb.errors import DependencyMissingError
from corpus_kb.eval.report import EVAL_EXTRA_REMEDIATION
from corpus_kb.rag.judge import JUDGE_TIMEOUT_SECONDS

__all__ = [
    "EvalDependencyError",
    "answer_metrics_available",
    "build_answer_metrics",
    "score_answer",
]

# The four metric names score_answer reports, in build order.
METRIC_NAMES = (
    "faithfulness",
    "answer_relevancy",
    "contextual_recall",
    "contextual_precision",
)


class EvalDependencyError(DependencyMissingError):
    """The ``[eval]`` extra is not installed."""

    remediation = EVAL_EXTRA_REMEDIATION


class _MetricsModule(Protocol):
    """The lazy ``deepeval.metrics`` surface (one factory per metric)."""

    def FaithfulnessMetric(  # noqa: N802 - mirrors the real library's name
        self, *, model: object
    ) -> object: ...

    def AnswerRelevancyMetric(  # noqa: N802
        self, *, model: object
    ) -> object: ...

    def ContextualRecallMetric(  # noqa: N802
        self, *, model: object
    ) -> object: ...

    def ContextualPrecisionMetric(  # noqa: N802
        self, *, model: object
    ) -> object: ...


class _ModelsModule(Protocol):
    """The lazy ``deepeval.models`` surface (the base LLM class)."""

    DeepEvalBaseLLM: type


class _TestCaseModule(Protocol):
    """The lazy ``deepeval.test_case`` surface."""

    def LLMTestCase(  # noqa: N802
        self,
        *,
        input: str,
        actual_output: str,
        expected_output: str | None = None,
        retrieval_context: list[str] | None = None,
    ) -> object: ...


class _Scorer(Protocol):
    """The one-metric surface score_answer touches."""

    def measure(self, case: object) -> float | None: ...


def _import_module(name: str) -> ModuleType:
    """The one import seam (tests substitute a ModuleNotFoundError here)."""
    return importlib.import_module(name)


def answer_metrics_available() -> bool:
    """True when deepeval imports (the ``[eval]`` extra is installed)."""
    try:
        _import_module("deepeval.metrics")
    except ModuleNotFoundError:
        return False
    return True


def build_answer_metrics(cfg: dict[str, object], judge_llm: object | None = None) -> list[object]:
    """Build the four answer metrics over a LOCAL Ollama judge.

    ``judge_llm`` substitutes any pre-built model object (tests pass a
    plain object; production leaves it None). Raises
    ``EvalDependencyError`` when the extra is absent.
    """
    metrics_module = cast(_MetricsModule, _require_deepeval("deepeval.metrics"))
    model = judge_llm if judge_llm is not None else _build_judge_llm(cfg)
    return [
        metrics_module.FaithfulnessMetric(model=model),
        metrics_module.AnswerRelevancyMetric(model=model),
        metrics_module.ContextualRecallMetric(model=model),
        metrics_module.ContextualPrecisionMetric(model=model),
    ]


def score_answer(
    query: str,
    answer: str,
    expected_answer: str,
    contexts: list[str],
    metrics: list[object],
) -> dict[str, float | None]:
    """Measure every metric on one Q/A case; a failing metric scores None.

    The case shape is deepeval's: the question, the produced answer, the
    golden reference answer, and the retrieved texts as
    ``retrieval_context``. Per-metric failures degrade to None — one bad
    metric must not sink the whole run, and None is not a score.
    """
    test_case_module = cast(_TestCaseModule, _require_deepeval("deepeval.test_case"))
    case = test_case_module.LLMTestCase(
        input=query,
        actual_output=answer,
        expected_output=expected_answer,
        retrieval_context=list(contexts),
    )
    scores: dict[str, float | None] = {}
    for name, metric in zip(METRIC_NAMES, metrics, strict=False):
        scorer = cast(_Scorer, metric)
        try:
            measured = scorer.measure(case)
            score = float(measured) if measured is not None else _metric_score(metric)
        except Exception:
            score = None
        scores[name] = score
    return scores


def _metric_score(metric: object) -> float | None:
    """deepeval also mirrors the last score onto the metric object."""
    raw = getattr(metric, "score", None)
    return None if raw is None else float(raw)


def _require_deepeval(module_name: str) -> ModuleType:
    """Import one deepeval module or raise the typed dependency error."""
    try:
        return _import_module(module_name)
    except ModuleNotFoundError as exc:
        raise EvalDependencyError(
            "The answer metrics need the deepeval package (the [eval] extra)."
        ) from exc


def _build_judge_llm(cfg: dict[str, object]) -> object:
    """The local-Ollama DeepEvalBaseLLM (subclass built after the import)."""
    models_module = cast(_ModelsModule, _require_deepeval("deepeval.models"))
    llm = cast(dict[str, object], cfg.get("llm", {}) or {})
    model_name = str(llm.get("model", "qwen3:4b"))
    base_url = str(llm.get("base_url", "http://localhost:11434"))
    base_cls = cast("type", models_module.DeepEvalBaseLLM)

    class OllamaJudgeLLM(base_cls):
        """deepeval model adapter over ``ollama.Client.chat`` (temp 0)."""

        def __init__(self, name: str, url: str) -> None:
            self._name = name
            self._client = Client(host=url, timeout=JUDGE_TIMEOUT_SECONDS)

        def load_model(self) -> Client:
            return self._client

        def generate(self, prompt: str) -> str:
            response = self._client.chat(
                model=self._name,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0},
            )
            message = response.get("message", {}) if isinstance(response, dict) else {}
            return str(message.get("content", ""))

        def load_responses(self, prompt: str) -> str:
            """The base-class batch alias: one prompt in, model text out."""
            return self.generate(prompt)

        async def a_generate(self, prompt: str) -> str:
            return self.generate(prompt)

        def get_model_name(self) -> str:
            return self._name

    return OllamaJudgeLLM(model_name, base_url)
