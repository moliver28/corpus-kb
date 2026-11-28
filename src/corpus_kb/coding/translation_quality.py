"""Translation quality (TD-12): LaBSE cosine + fastText LID; honest not_measured (AM-05)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from corpus_kb.coding._quality_types import (
    EncoderLike,
    import_fasttext,
    import_sentence_transformers,
)
from corpus_kb.errors import DependencyMissingError

# Cached LaBSE encoder (momus, T1 fix round): one process-wide instance
# instead of one per assess_translation call. None = the extra is absent.
_LABSE_MODEL: EncoderLike | None = None

__all__ = [
    "QUALITY_CONFIG_DEFAULTS",
    "TranslatePort",
    "TranslationUnavailableError",
    "W6BPort",
    "assess_translation",
]

MIN_QUALITY_COSINE_KEY = "coding.quality.min_quality_cosine"
LID_WEIGHTS_KEY = "coding.quality.lid_weights_path"

#: Registry rows for the exact artifacts this module names (AM-05, checked 2026-11-26).
#: The 217-language HF fastText variant is CC-BY-NC-4.0 — a different artifact, never a default.
LABSE_LICENSE = "Apache-2.0"
LID_LICENSE = "CC-BY-SA-3.0"

QUALITY_CONFIG_DEFAULTS: dict[str, Any] = {
    "min_quality_cosine": 0.80,
    "lid_weights_path": "",
}

STATE_OK = "ok"
STATE_BELOW_FLOOR = "below_floor"
STATE_NOT_MEASURED = "not_measured"


class TranslationUnavailableError(DependencyMissingError):
    pass


class TranslatePort(Protocol):
    """Consumer-side seam over the W6B general translate contract (D-2)."""

    async def translate(self, text: str, target_language: str, backend: str = "argos") -> str: ...


class W6BPort:
    """Default adapter: raises until the W6B merge fills the seam."""

    async def translate(self, text: str, target_language: str, backend: str = "argos") -> str:
        raise TranslationUnavailableError("No translate backend is wired (W6B not merged).")


@dataclass(frozen=True)
class QualityResult:
    state: str
    score: float | None
    lid_probability: float | None = None


def _cosine(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if not norm_a or not norm_b:
        return None
    return dot / (norm_a * norm_b)


def _labse_encoder() -> EncoderLike | None:
    """Build the LaBSE encoder once; None when the [quality] extra is absent.

    The extras-only import stays inside this factory (through the typed
    ``_quality_types`` seam), so an environment without the extra keeps
    the honest not_measured path.
    """
    global _LABSE_MODEL
    if _LABSE_MODEL is None:
        module = import_sentence_transformers()
        if module is None:
            return None
        _LABSE_MODEL = module.SentenceTransformer("sentence-transformers/LaBSE")
    return _LABSE_MODEL


def _embed_pair(source: str, translation: str) -> list[list[float]] | None:
    model = _labse_encoder()
    if model is None:
        return None
    return [list(map(float, v)) for v in model.encode([source, translation])]


def _lid_probability(text: str, weights_path: str) -> float | None:
    if not weights_path:
        return None
    module = import_fasttext()
    if module is None:
        return None
    try:
        detector = module.load_model(weights_path)
        labels, probabilities = detector.predict(text.replace("\n", " "))
    except Exception:
        return None
    if not labels or not probabilities:
        return None
    return float(probabilities[0])


def assess_translation(
    source: str,
    translation: str,
    quality_cfg: Mapping[str, Any] | None = None,
) -> QualityResult:
    """Score one translation; not_measured whenever a piece is absent."""
    if not translation.strip():
        return QualityResult(STATE_NOT_MEASURED, None, None)
    cfg = quality_cfg or QUALITY_CONFIG_DEFAULTS
    embeddings = _embed_pair(source, translation)
    if embeddings is None:
        return QualityResult(STATE_NOT_MEASURED, None, None)
    score = _cosine(embeddings[0], embeddings[1])
    if score is None:
        return QualityResult(STATE_NOT_MEASURED, None, None)
    floor = float(cfg.get("min_quality_cosine", QUALITY_CONFIG_DEFAULTS["min_quality_cosine"]))
    lid = _lid_probability(text=source, weights_path=str(cfg.get("lid_weights_path") or ""))
    state = STATE_OK if score >= floor else STATE_BELOW_FLOOR
    return QualityResult(state, score, lid)
