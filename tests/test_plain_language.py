"""Plain-language gate, zero-drift Wave 5A (ASD-STE100, program standard 6).

Vale (``.vale.ini`` + ``styles/CorpusSTE``) lints the Markdown; it cannot
reach Python strings. This module is the Python half of the gate. Run it
with ``pytest -m ste`` (marker registered in ``pyproject.toml``).

Three checks:

(a) Docs + README: Flesch-Kincaid grade <= 12 per document, and no
    sentence over 30 words inside a quickstart section. Code fences, inline
    code, and HTML are stripped before grading — the gate grades prose.
    The program's binding target is grade <= 9; that ratchet binds the
    Wave 5B REWRITE of the README/docs and is enforced per-file in Wave
    5B's package, not here. Files Wave 5B owns are skipped with a named
    reason until that rewrite lands.
(b) Error taxonomy: every ``CorpusError`` subclass (the ``ERROR_MAP``
    consumers in ``corpus_kb/errors.py``) must expose a ``remediation``
    whose sentences are 20 words or fewer and carry no banned idiom.
(c) Registered MCP tools: the docstring of every tool the ``mcp_tools``
    registry resolves must exist, and its first sentence must be 25 words
    or fewer.

The gate must pass on the tree as committed. Fix flagged strings in your
own scope; list out-of-scope findings in the wave report instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import textstat

pytestmark = pytest.mark.ste

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Shared rules
# ---------------------------------------------------------------------------

# ~15 banned idioms (STE word-choice + house rules). Word-bounded, case
# insensitive. "etc." matches with or without the trailing period.
BANNED_IDIOMS: tuple[str, ...] = (
    "garbage",
    "speak",
    "just",
    "simply",
    "obviously",
    "etc",
    "and so on",
    "please note",
    "in order to",
    "utilize",
    "leverage",
    "prior to",
    "subsequently",
    "in the event that",
    "due to the fact",
)


def _idiom_pattern() -> re.Pattern[str]:
    parts = [re.escape(term).replace(r"\ ", r"\s+") for term in BANNED_IDIOMS]
    etc = r"\betc\b\.?"
    return re.compile(f"(?i)\\b(?:{'|'.join(parts)})\\b|{etc}")


IDIOM_RX = _idiom_pattern()


def _words(sentence: str) -> int:
    return len([w for w in sentence.split() if re.search(r"[A-Za-z0-9]", w)])


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _strip_markdown(md: str) -> str:
    """Fenced code, inline code, HTML tags, and link URLs out; prose stays."""
    md = re.sub(r"```.*?```", " ", md, flags=re.S)
    md = re.sub(r"`[^`\n]*`", " code ", md)
    md = re.sub(r"<[^>\n]+>", " ", md)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    return md


# ---------------------------------------------------------------------------
# (a) Docs + README
# ---------------------------------------------------------------------------

# Wave 5B owns the README + docs rewrite (program target: grade <= 9 there).
# A file listed here is skipped with its measured grade until that rewrite
# lands. A file NOT listed here that exceeds the grade bound FAILS this gate.
PENDING_W5B_REWRITE: dict[str, str] = {
    "README.md": "Wave 5B rewrites the README quickstart (ratchet target: grade <= 9).",
    "harness-distribution.md": "Wave 5B rewrites the distribution doc.",
}

MAX_DOC_GRADE = 12
MAX_QUICKSTART_SENTENCE_WORDS = 30


def _doc_paths() -> list[Path]:
    return sorted((REPO / "docs").glob("*.md")) + [REPO / "README.md"]


@pytest.mark.parametrize("path", _doc_paths(), ids=lambda p: p.name)
def test_doc_readability_grade(path: Path) -> None:
    """Every doc grades at or below the technical ceiling (FK <= 12)."""
    prose = _strip_markdown(path.read_text(encoding="utf-8", errors="replace"))
    grade = float(textstat.flesch_kincaid_grade(prose))
    reason = PENDING_W5B_REWRITE.get(path.name)
    if reason is not None and grade > MAX_DOC_GRADE:
        pytest.skip(f"W5B pending rewrite: FK grade {grade:.1f} > {MAX_DOC_GRADE}. {reason}")
    assert grade <= MAX_DOC_GRADE, (
        f"{path.name}: FK grade {grade:.1f} > {MAX_DOC_GRADE}. Use shorter, "
        "everyday sentences."
    )


def test_quickstart_sentences_short() -> None:
    """No sentence over 30 words inside any quickstart/getting-started section."""
    offenders: list[str] = []
    for path in _doc_paths():
        raw = path.read_text(encoding="utf-8", errors="replace")
        parts = re.split(r"^(#{1,6}\s+.+)$", raw, flags=re.M)
        for i in range(1, len(parts) - 1, 2):
            heading = parts[i].lstrip("#").strip()
            if not re.search(r"quick\s*start|getting\s+started", heading, re.IGNORECASE):
                continue
            prose = _strip_markdown(parts[i + 1])
            for sentence in _sentences(prose):
                if _words(sentence) > MAX_QUICKSTART_SENTENCE_WORDS:
                    offenders.append(f"{path.name} [{heading}]: {sentence.strip()[:120]}")
    assert not offenders, (
        "Quickstart sentences over "
        f"{MAX_QUICKSTART_SENTENCE_WORDS} words: {offenders}"
    )


# ---------------------------------------------------------------------------
# (b) Error taxonomy remediations
# ---------------------------------------------------------------------------


def _corpus_error_subclasses() -> list[type]:
    import corpus_kb.errors as errors_module
    from corpus_kb.errors import CorpusError

    return [
        obj
        for obj in vars(errors_module).values()
        if isinstance(obj, type) and issubclass(obj, CorpusError)
    ]


@pytest.mark.parametrize("cls", _corpus_error_subclasses(), ids=lambda c: c.__name__)
def test_error_remediation_plain_language(cls: type) -> None:
    """Remediation text: sentences <= 20 words, no banned idioms, str() honest."""
    from corpus_kb.errors import CorpusError

    assert issubclass(cls, CorpusError)
    instance = cls("probe message")
    # str() round-trips the message the raise site passes (taxonomy contract).
    assert str(instance) == "probe message"
    remediation = instance.remediation
    assert remediation.strip(), f"{cls.__name__} carries an empty remediation"
    for sentence in _sentences(remediation):
        assert _words(sentence) <= 20, (
            f"{cls.__name__} remediation sentence over 20 words: {sentence!r}"
        )
    hit = IDIOM_RX.search(remediation)
    assert hit is None, f"{cls.__name__} remediation uses banned idiom {hit.group(0)!r}"


def test_base_class_default_remediation_plain_language() -> None:
    """The CorpusError base remediation follows the same rules."""
    from corpus_kb.errors import CorpusError

    remediation = CorpusError.remediation
    for sentence in _sentences(remediation):
        assert _words(sentence) <= 20, f"base remediation sentence over 20 words: {sentence!r}"
    hit = IDIOM_RX.search(remediation)
    assert hit is None, f"base remediation uses banned idiom {hit.group(0)!r}"


# ---------------------------------------------------------------------------
# (c) Registered MCP tool docstrings
# ---------------------------------------------------------------------------


def _registered_tool_functions() -> list[tuple[str, object]]:
    from corpus_kb.contracts import OPERATIONS
    from corpus_kb.contracts.model import MCP, surfaces_of
    from corpus_kb.mcp_tools import REGISTRY_TOOL_FUNCTIONS, TOOL_LAYER_OVERRIDES, resolve_function

    specs: dict[str, str] = {}
    for op in OPERATIONS:
        mcp = surfaces_of(op, MCP)
        if not mcp:
            continue
        specs[mcp[0].route] = TOOL_LAYER_OVERRIDES.get(op.name, op.handler)
    for name, spec in REGISTRY_TOOL_FUNCTIONS.items():
        specs.setdefault(name, spec)
    return [(name, resolve_function(spec)) for name, spec in sorted(specs.items())]


MAX_TOOL_FIRST_SENTENCE_WORDS = 25


_tool_fns = _registered_tool_functions()


@pytest.mark.parametrize(("name", "fn"), _tool_fns, ids=[name for name, _ in _tool_fns])
def test_tool_docstring_first_sentence(name: str, fn: object) -> None:
    """Every registered tool documents itself: first sentence <= 25 words."""
    doc = getattr(fn, "__doc__", None)
    assert doc and doc.strip(), f"tool {name} ({fn}) has no docstring"
    flat = " ".join(doc.strip().split())
    first = _sentences(flat)[0]
    assert _words(first) <= MAX_TOOL_FIRST_SENTENCE_WORDS, (
        f"tool {name}: first docstring sentence is {_words(first)} words "
        f"(max {MAX_TOOL_FIRST_SENTENCE_WORDS}): {first[:120]}"
    )
