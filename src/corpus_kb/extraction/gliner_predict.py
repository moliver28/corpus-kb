"""GLiNER span prediction with a verified batch fast path.

``gliner2 >= 2.1`` exposes ``batch_extract_entities(texts, schema)`` next to
the per-text ``extract_entities``. Batching changes inference padding, so
scores can drift past the span threshold and flip an output span. Drift is
not acceptable here (deterministic output is a program standard), so the
first batch is verified against the per-text path: only an exact match
promotes the batch path, and any mismatch locks the predictor to per-text
calls for the process lifetime. Worst case equals the old loop exactly.
"""

from __future__ import annotations

import logging
from typing import cast

from ._gliner2_types import GlinerBatchModel, GlinerModel, GlinerSpan

logger = logging.getLogger(__name__)


def normalize_spans(raw: dict[str, list[object]]) -> list[GlinerSpan]:
    """Flatten a ``{label: [span, ...]}`` payload into normalized spans.

    gliner2 span payloads have been observed as both plain dicts
    (``{"text", "start", "end", "score", "label"}``) and small objects with
    those attributes; both are handled here.
    """
    spans: list[GlinerSpan] = []
    for bucket in raw.values():
        for item in bucket:
            if isinstance(item, dict):
                spans.append(_DictSpan(cast(dict[str, object], item)))
            else:
                spans.append(cast(GlinerSpan, item))
    return spans


def span_key(span: GlinerSpan) -> tuple[object, ...]:
    """The comparison key for the batch-vs-per-text equivalence gate."""
    return (span.text, span.start, span.end, span.score, span.label)


class _DictSpan:
    """Attribute-view over a dict-shaped gliner span payload."""

    __slots__ = ("end", "label", "score", "start", "text")

    def __init__(self, data: dict[str, object]) -> None:
        self.text = str(data.get("text", ""))
        self.start = _as_int(data.get("start"))
        self.end = _as_int(data.get("end"))
        self.score = _as_float(data.get("score"))
        self.label = str(data.get("label", data.get("entity_group", "")))


def _as_int(value: object) -> int:
    """Coerce a JSON-ish object to int, 0 when not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _as_float(value: object) -> float:
    """Coerce a JSON-ish object to float, 0.0 when not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


class GlinerPredictor:
    """Per-text span prediction; the batch path is earned, not assumed."""

    def __init__(self, model: GlinerModel, schema: dict[str, str]) -> None:
        self._model = model
        self._schema = schema
        self._batch_ok: bool | None = None

    def predict(self, texts: list[str]) -> list[list[GlinerSpan]]:
        """Spans per text, in input order. Batched only when verified equal."""
        if not texts:
            return []
        per_text = None
        batched = self._batch_predict(texts)
        if self._batch_ok is False or batched is None:
            return [self._predict_one(text) for text in texts]
        if self._batch_ok is None:
            per_text = [self._predict_one(text) for text in texts]
            if not _equal(batched, per_text):
                logger.warning(
                    "gliner batch output drifts from per-text output; "
                    "keeping the per-text path for this process"
                )
                self._batch_ok = False
                return per_text
            self._batch_ok = True
        return batched

    def _predict_one(self, text: str) -> list[GlinerSpan]:
        raw = self._model.extract_entities(text, self._schema)
        return normalize_spans(raw)

    def _batch_predict(self, texts: list[str]) -> list[list[GlinerSpan]] | None:
        """One batched call, or None when the model has no batch API."""
        if self._batch_ok is False:
            return None
        model = self._model
        if not isinstance(model, GlinerBatchModel):
            return None
        try:
            raw_list = model.batch_extract_entities(texts, self._schema)
        except Exception as exc:
            logger.warning("gliner batch call failed (%s); using per-text", exc)
            return None
        if len(raw_list) != len(texts):
            logger.warning("gliner batch returned %d of %d results", len(raw_list), len(texts))
            return None
        return [normalize_spans(raw) for raw in raw_list]


def _equal(batched: list[list[GlinerSpan]], per_text: list[list[GlinerSpan]]) -> bool:
    """Exact span-multiset equality per text (bucket order is not a fact)."""
    if len(batched) != len(per_text):
        return False
    for batch_spans, text_spans in zip(batched, per_text, strict=False):
        if sorted(span_key(s) for s in batch_spans) != sorted(span_key(s) for s in text_spans):
            return False
    return True
