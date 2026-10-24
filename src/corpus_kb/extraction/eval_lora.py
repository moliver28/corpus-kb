"""LoRA adapter scoring against the promotion gate (Living Corpus W7).

The consumer of the W5 tune artifacts: load the base GLiNER, score it
on the gold set (the baseline), apply the saved adapter
(``lora_tune.apply_lora_adapter``), score again (the candidate), and
run the pinned promotion gate (micro F1 >= 0.80 AND >= baseline +
0.02). Promoted adapters are the ONLY ones live extraction may use.

Span prediction goes through the gliner package's ``predict_entities``
(the same pkg the adapter was trained on). All heavy imports are lazy
and typed-error-gated, mirroring ``lora_tune``.
"""

from __future__ import annotations

from typing import Any

from ._eval_gold import GoldDoc, GoldSpan, load_gold
from .eval import EvalReport, PromotionDecision, promotion_gate, score_corpus
from .lora_tune import apply_lora_adapter, tune_settings

__all__ = [
    "AdapterScoreResult",
    "predict_spans",
    "score_adapter",
]


def _gliner_cls() -> Any:
    """Lazy gliner import (the tune extra)."""
    import importlib

    try:
        return importlib.import_module("gliner").GLiNER
    except ImportError as exc:
        from ..errors import DependencyMissingError

        raise DependencyMissingError(
            f"Scoring a LoRA adapter needs the tune extra: pip install 'corpus-kb[tune]' ({exc}).",
            remediation="Install the tune extra, or score with the zero-shot backend instead.",
        ) from exc


def predict_spans(
    model: Any, docs: list[GoldDoc], threshold: float = 0.5
) -> dict[str, list[GoldSpan]]:
    """GoldDoc texts -> predicted spans keyed by doc_id (gliner pkg API)."""
    predictions: dict[str, list[GoldSpan]] = {}
    for doc in docs:
        raw = model.predict_entities(doc.text, labels=None, threshold=threshold)
        spans: list[GoldSpan] = []
        for entity in raw or []:
            start, end = int(entity["start"]), int(entity["end"])
            label = str(entity.get("label", entity.get("type", "")))
            spans.append(GoldSpan(start=start, end=end, label=label))
        predictions[doc.doc_id] = spans
    return predictions


class AdapterScoreResult:
    """Baseline + candidate reports and the promotion decision."""

    __slots__ = ("baseline", "candidate", "decision")

    def __init__(
        self,
        baseline: EvalReport,
        candidate: EvalReport,
        decision: PromotionDecision,
    ) -> None:
        self.baseline = baseline
        self.candidate = candidate
        self.decision = decision


def score_adapter(
    adapter_dir: str,
    gold_path: str,
    config: dict[str, Any] | None = None,
) -> AdapterScoreResult:
    """Score a saved adapter vs its base model on the gold set.

    Deterministic given the model and the gold file: one load per model
    variant, doc order preserved, span order as the model returns it.
    """
    gold = load_gold(gold_path)
    settings = tune_settings(config or {})
    gliner_cls = _gliner_cls()

    base_model = gliner_cls.from_pretrained(str(settings["model"]))
    baseline = score_corpus(gold, predict_spans(base_model, gold))

    adapted = apply_lora_adapter(gliner_cls.from_pretrained(str(settings["model"])), adapter_dir)
    candidate = score_corpus(gold, predict_spans(adapted, gold))

    decision = promotion_decision(baseline, candidate)
    return AdapterScoreResult(baseline, candidate, decision)


def promotion_decision(baseline: EvalReport, candidate: EvalReport) -> PromotionDecision:
    """The pinned gate over the two micro F1s (eval.promotion_gate)."""
    return promotion_gate(candidate.micro.f1, baseline.micro.f1)
