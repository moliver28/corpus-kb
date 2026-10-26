"""Plain-language gate for the SPA string table (UI SPA program Task 4).

Zero-drift amendment 2 extends the ASD-STE100 rule to the SPA: every
user-facing string lives in ``ui_src/src/strings/en.json`` and this file
gates it (the mirror of ``tests/test_plain_language.py`` for Python
strings; the helper functions are reused from there).

Rules per string: sentences of at most 20 words, no banned idiom, and
``{placeholders}`` stripped before scoring. The whole table must read at
Flesch-Kincaid grade 9 or below.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_plain_language import IDIOM_RX, _sentences, _words

pytestmark = pytest.mark.ste

REPO = Path(__file__).resolve().parents[1]
EN_JSON = REPO / "ui_src" / "src" / "strings" / "en.json"
MAX_SENTENCE_WORDS = 20
MAX_GRADE = 9.0

_PLACEHOLDER_RX = re.compile(r"\{[^{}]*\}")


def _strings() -> dict[str, str]:
    data = json.loads(EN_JSON.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "en.json must be a flat string table"
    return {str(key): str(value) for key, value in data.items()}


@pytest.mark.parametrize("key", sorted(_strings()))
def test_string_is_short_plain_english(key: str) -> None:
    """Each string: sentences <= 20 words, no banned idiom, placeholders out."""
    text = _PLACEHOLDER_RX.sub("", _strings()[key])
    for sentence in _sentences(text):
        assert _words(sentence) <= MAX_SENTENCE_WORDS, (
            f"en.json[{key}]: sentence over {MAX_SENTENCE_WORDS} words: {sentence!r}"
        )
    hit = IDIOM_RX.search(text)
    assert hit is None, f"en.json[{key}] uses banned idiom {hit.group(0)!r}"


def test_string_table_reads_at_grade_nine() -> None:
    """The whole table grades at or below the program's plain-English bar."""
    import textstat

    prose = _PLACEHOLDER_RX.sub("", " ".join(_strings().values()))
    grade = float(textstat.flesch_kincaid_grade(prose))
    assert grade <= MAX_GRADE, (
        f"en.json grades FK {grade:.1f} > {MAX_GRADE}. Use shorter, everyday sentences."
    )
