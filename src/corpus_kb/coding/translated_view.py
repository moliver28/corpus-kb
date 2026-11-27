"""Read-only translated view (TD-12 point 5; refine D-3 narrowing).

The coder reads a translation; it never translates on its own path (N5 as
narrowed) and no surface ships in T1 (op budget N11: with W6B unmerged
there is no translation cache, so any surface key would render ``missing``
forever). A cache miss is ``missing`` and the coder requests re-ingest
through the queue; a hit carries the text, the languages, and the quality
assessment. The cache is the W6B content-hash-keyed ``translation_cache``
(D12b).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from corpus_kb.coding.translation_quality import assess_translation

__all__ = ["TranslatedView", "TranslationCache", "translated_view"]

STATE_MISSING = "missing"


class TranslationCache(Protocol):
    """The content-hash-keyed cache seam (D12b); a Mapping of hash -> row."""

    def get(self, key: str) -> Mapping[str, Any] | None:
        """One cached translation row, or None on a miss."""
        ...


@dataclass(frozen=True)
class TranslatedView:
    """One unit's read-only translated state (TD-12 point 5)."""

    unit_id: str
    text: str
    source_language: str
    target_language: str
    quality_state: str
    quality_score: float | None


def _cache_key(source_text: str, target_language: str) -> str:
    """Content hash over the source text and the requested language."""
    payload = f"{target_language}\n{source_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def translated_view(
    unit_id: str,
    source_text: str,
    target_language: str,
    cache: TranslationCache | None,
    quality_cfg: Mapping[str, Any] | None = None,
) -> TranslatedView:
    """One unit's translated view; a cache miss answers ``missing``.

    The translate operation itself is W6B's general op: re-translation
    rides re-ingest plus the cache, never a coder-side call (TD-12).
    """
    if cache is None:
        return TranslatedView(unit_id, "", "", target_language, STATE_MISSING, None)
    row = cache.get(_cache_key(source_text, target_language))
    if row is None:
        return TranslatedView(unit_id, "", "", target_language, STATE_MISSING, None)
    source = str(row.get("source_text") or source_text)
    text = str(row.get("text") or "")
    quality = assess_translation(source, text, quality_cfg)
    return TranslatedView(
        unit_id=unit_id,
        text=text,
        source_language=str(row.get("source_language") or ""),
        target_language=str(row.get("target_language") or target_language),
        quality_state=quality.state,
        quality_score=quality.score,
    )
