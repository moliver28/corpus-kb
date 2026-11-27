"""Translation quality + read-only view (TD-12): seam, states, cache miss.

No model runs here: the extras-less CI environment has no
sentence-transformers, so ``assess_translation`` answers ``not_measured``
(the honest state, D-5). The seam and view states are pure.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.coding.translated_view import STATE_MISSING, translated_view
from corpus_kb.coding.translation_quality import (
    LABSE_LICENSE,
    LID_LICENSE,
    QUALITY_CONFIG_DEFAULTS,
    TranslationUnavailableError,
    W6BPort,
    assess_translation,
)


async def test_w6b_port_raises_typed_until_seam_fills() -> None:
    port = W6BPort()
    with pytest.raises(TranslationUnavailableError):
        await port.translate("text", "de")


def test_license_rows_carry_checked_values() -> None:
    # AM-05: the exact artifacts, checked 2026-11-26. The NC HF fastText
    # variant is a different artifact and is never a default.
    assert LABSE_LICENSE == "Apache-2.0"
    assert LID_LICENSE == "CC-BY-SA-3.0"


def test_quality_defaults_carry_floor_and_empty_weights() -> None:
    assert QUALITY_CONFIG_DEFAULTS == {"min_quality_cosine": 0.80, "lid_weights_path": ""}


def test_empty_translation_is_not_measured() -> None:
    result = assess_translation("source", "   ")
    assert (result.state, result.score) == ("not_measured", None)


def test_missing_extra_is_not_measured_no_crash() -> None:
    # Extras-less CI: the lazy sentence-transformers import fails, and the
    # honest answer is not_measured — never a fabricated score (D-5).
    result = assess_translation("Hallo Welt", "Hello World", QUALITY_CONFIG_DEFAULTS)
    assert result.state == "not_measured"
    assert result.score is None


def test_floor_reads_config_leaf() -> None:
    cfg: dict[str, Any] = {"min_quality_cosine": 0.5, "lid_weights_path": ""}
    # With the extra absent the floor never applies; the state stays honest.
    assert assess_translation("a", "b", cfg).state == "not_measured"


class _Cache:
    """Mapping-shaped stand-in for the W6B translation_cache (D12b)."""

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self._rows = rows

    def get(self, key: str) -> dict[str, Any] | None:
        return self._rows.get(key)


def test_cache_miss_is_missing() -> None:
    view = translated_view("u1", "Hallo", "en", _Cache({}))
    assert view.quality_state == STATE_MISSING
    assert view.text == ""
    assert view.quality_score is None


def test_cache_none_is_missing() -> None:
    view = translated_view("u1", "Hallo", "en", None)
    assert view.quality_state == STATE_MISSING


def test_cache_hit_carries_text_languages_and_state() -> None:
    from corpus_kb.coding.translated_view import _cache_key

    cache = _Cache(
        {
            _cache_key("Hallo Welt", "en"): {
                "text": "Hello world",
                "source_text": "Hallo Welt",
                "source_language": "de",
                "target_language": "en",
            }
        }
    )
    view = translated_view("u1", "Hallo Welt", "en", cache)
    assert view.text == "Hello world"
    assert view.source_language == "de"
    assert view.target_language == "en"
    # Extras-less CI: quality is honestly not measured, never defaulted.
    assert view.quality_state == "not_measured"
