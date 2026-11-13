"""Plain-language gate for the SPA string tables (UI SPA program Task 4).

Zero-drift amendment 2 extends the ASD-STE100 rule to the SPA. Phase-2
Wave 6 split the one ``en.json`` table into per-stage lazy tables (one
``strings/<stage>.json`` per stage chunk; the entry chunk carries only
``app.json``), so this file walks every ``strings/*.json`` table — the
mirror of ``tests/test_plain_language.py`` for Python strings; the
helper functions are reused from there.

Rules per string: sentences of at most 20 words, no banned idiom, and
``{placeholders}`` stripped before scoring. The whole vocabulary must
read at Flesch-Kincaid grade 9 or below. A second check pins the
strings lockstep: every JSON key must appear in its stage module's
interface, so a key cannot land on one side alone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_plain_language import IDIOM_RX, _sentences, _words

pytestmark = pytest.mark.ste

REPO = Path(__file__).resolve().parents[1]
STRINGS_DIR = REPO / "ui_src" / "src" / "strings"
MAX_SENTENCE_WORDS = 20
MAX_GRADE = 9.0

_PLACEHOLDER_RX = re.compile(r"\{[^{}]*\}")


def _tables() -> dict[str, dict[str, str]]:
    """Every per-stage table, keyed by its stage name (app, graph, ...)."""
    out: dict[str, dict[str, str]] = {}
    for path in sorted(STRINGS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name} must be a flat string table"
        out[path.stem] = {str(key): str(value) for key, value in data.items()}
    assert out, "no string tables found under ui_src/src/strings"
    return out


def _pairs() -> list[str]:
    """Stable ``table::key`` ids, one per string across every table."""
    return [
        f"{table}::{key}"
        for table in sorted(_tables())
        for key in sorted(_tables()[table])
    ]


def _string_of(pair: str) -> str:
    table, key = pair.split("::", 1)
    return _tables()[table][key]


@pytest.mark.parametrize("pair", _pairs())
def test_string_is_short_plain_english(pair: str) -> None:
    """Each string: sentences <= 20 words, no banned idiom, placeholders out."""
    text = _PLACEHOLDER_RX.sub("", _string_of(pair))
    for sentence in _sentences(text):
        assert _words(sentence) <= MAX_SENTENCE_WORDS, (
            f"{pair}: sentence over {MAX_SENTENCE_WORDS} words: {sentence!r}"
        )
    hit = IDIOM_RX.search(text)
    assert hit is None, f"{pair} uses banned idiom {hit.group(0)!r}"


def test_string_tables_read_at_grade_nine() -> None:
    """The whole vocabulary grades at or below the program's plain-English bar."""
    import textstat

    merged = [value for table in _tables().values() for value in table.values()]
    prose = _PLACEHOLDER_RX.sub("", " ".join(merged))
    grade = float(textstat.flesch_kincaid_grade(prose))
    assert grade <= MAX_GRADE, (
        f"string tables grade FK {grade:.1f} > {MAX_GRADE}. Use shorter, everyday sentences."
    )


@pytest.mark.parametrize("table", sorted(_tables()))
def test_table_keys_land_in_their_interface(table: str) -> None:
    """Strings lockstep: every JSON key sits in the stage module interface."""
    interface = (STRINGS_DIR / f"{table}.ts").read_text(encoding="utf-8")
    missing = [
        key
        for key in _tables()[table]
        if not re.search(rf"^\s+{re.escape(key)}: string;$", interface, flags=re.MULTILINE)
    ]
    assert missing == [], f"{table}.ts interface misses keys: {missing}"


def test_union_interface_lists_every_stage() -> None:
    """The tests-only union extends every stage interface."""
    index = (STRINGS_DIR / "index.ts").read_text(encoding="utf-8")
    for table in sorted(_tables()):
        name = table[0].upper() + table[1:] + "Strings"
        assert re.search(rf"\b{name}\b", index), f"strings/index.ts misses {name}"
