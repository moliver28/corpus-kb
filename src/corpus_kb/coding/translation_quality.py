"""Translation quality signal (TD-12 point 3, R8): cosine + LID, in-module.

Cosine between source and translation in a multilingual sentence space
(LaBSE) plus a language-ID probability computed here (fastText
``lid.176.bin``); no W6B dependency — W6B's detect stage stores a code,
not a probability (athena G-13). Licenses (AM-05, 2026-11-26): LaBSE
Apache-2.0; ``lid.176.bin`` CC-BY-SA 3.0. The 217-language HF fastText
variant is CC-BY-NC-4.0 — a different artifact, never a default. A
missing extra, weights file, or failed import answers ``not_measured`` —
no crash, no fabricated score. Extra ``[quality]``: lazy imports,
extras-less CI stays green (R18, ``latechunk`` precedent). A score below
``coding.quality.min_quality_cosine`` blocks grade A and routes to a
bilingual reader (owned convention, S10; floor 0.80, D-4). The translate
operation is W6B's general op; this module only scores and gates.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from corpus_kb.errors import DependencyMissingError

__all__ = [
    "QUALITY_CONFIG_DEFAULTS",
    "TranslatePort",
    "TranslationUnavailableError",
    "W6BPort",
    "assess_translation",
]

MIN_QUALITY_COSINE_KEY = "coding.quality.min_quality_cosine"
LID_WEIGHTS_KEY = "coding.quality.lid_weights_path"

#: Registry rows for the exact artifacts this module names (AM-05).
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
    """The W6B translate op is not merged; no translate backend is wired."""


class TranslatePort(Protocol):
    """Consumer-side seam over the W6B general translate contract (D-2).

    Text in, target language, backend choice (argos default local); the
    W6B merge supplies the implementation and its cache.
    """

    async def translate(self, text: str, target_language: str, backend: str = "argos") -> str:
        """Translate one text; answers the translation."""
        ...


class W6BPort:
    """Default adapter: raises until the W6B merge fills the seam."""

    async def translate(self, text: str, target_language: str, backend: str = "argos") -> str:
        """Never translates: the seam stays empty until W6B lands."""
        raise TranslationUnavailableError(
            "No translate backend is wired; the W6B translate operation is not merged."
        )


@dataclass(frozen=True)
class QualityResult:
    """One assessment: a state plus the measured score when it exists."""

    state: str
    score: float | None
    lid_probability: float | None = None


def _cosine(a: list[float], b: list[float]) -> float | None:
    """Cosine over two equal-length vectors; None on any zero vector."""
    if len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if not norm_a or not norm_b:
        return None
    return dot / (norm_a * norm_b)


def _embed_pair(source: str, translation: str) -> list[list[float]] | None:
    """LaBSE embeddings for the pair; None when the extra is absent."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    model = SentenceTransformer("sentence-transformers/LaBSE")
    return [list(map(float, v)) for v in model.encode([source, translation])]


def _lid_probability(text: str, weights_path: str) -> float | None:
    """fastText language-ID probability; None when weights or extra are absent."""
    if not weights_path:
        return None
    try:
        import fasttext
    except ImportError:
        return None
    try:
        detector = fasttext.load_model(weights_path)
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
    """Score one translation; ``not_measured`` whenever a piece is absent.

    An empty translation is not measurable either. The floor comes from
    ``coding.quality.min_quality_cosine`` (default 0.80, refine D-4).
    """
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
