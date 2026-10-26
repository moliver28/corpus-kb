"""Markdown heading segmenter (unified-ingestion Wave 5): sections + spans.

Offline: ATX/setext/fence segmentation, exact char spans on the canonical
document text, breadcrumb metadata, budget splits, and the detection rules
that keep markdown FILES on today's default path (byte-identical
envelopes) while raw heading-structured text stops being degenerate.
"""

from __future__ import annotations

from corpus_kb.ingestion.base import (
    DETECT_ADOPT_CONFIDENCE,
    TOKEN_BUDGET_CHARS,
    SourceProfile,
    validate_plan,
)
from corpus_kb.ingestion.registry import resolve_strategy
from corpus_kb.ingestion.strategies.markdown_sections import (
    MARKDOWN_SECTIONS_STRATEGY_NAME,
    MarkdownSectionsStrategy,
)

CFG_EMPTY: dict[str, object] = {"ingestion": {"strategies": {}, "default": "default"}}

MARKDOWN = (
    "# Alpha\n\nAlpha body text lives here.\n\n"
    "## Sub\n\nSub body text.\n\n"
    "# Beta\n\nBeta body text.\n"
)


def _profile(path: str = "raw_text", is_file: bool = False) -> SourceProfile:
    return SourceProfile(source_type="text", is_file=is_file, path=path)


# ---------------------------------------------------------------------------
# Detection: raw text claims, files stay on the default path
# ---------------------------------------------------------------------------


def test_detect_raw_text_with_atx_headings_adopts() -> None:
    result = MarkdownSectionsStrategy().detect("text", "raw_text", MARKDOWN)
    assert result.confidence >= DETECT_ADOPT_CONFIDENCE
    assert result.reason == "atx-headings"


def test_detect_md_file_never_claims() -> None:
    assert MarkdownSectionsStrategy().detect("markdown", "doc.md", MARKDOWN).confidence == 0.0
    assert MarkdownSectionsStrategy().detect("markdown", "doc.markdown", MARKDOWN).confidence == 0.0
    assert MarkdownSectionsStrategy().detect("markdown", "doc.rst", MARKDOWN).confidence == 0.0


def test_detect_code_suffix_never_claims() -> None:
    # '# ' lines in source files are comments, not headings.
    head = "# a comment\ndef main():\n    pass\n"
    assert MarkdownSectionsStrategy().detect("code", "script.py", head).confidence == 0.0


def test_detect_plain_prose_is_zero() -> None:
    assert MarkdownSectionsStrategy().detect("text", "raw_text", "Just prose.\n").confidence == 0.0


def test_detect_setext_headings_adopt() -> None:
    head = "Title\n=====\n\nBody text.\n"
    result = MarkdownSectionsStrategy().detect("text", "raw_text", head)
    assert result.reason == "setext-headings"
    assert result.confidence >= DETECT_ADOPT_CONFIDENCE


def test_resolution_adopts_for_raw_markdown_text() -> None:
    resolution = resolve_strategy("text", "raw_text", MARKDOWN, CFG_EMPTY)
    assert resolution.strategy.name == MARKDOWN_SECTIONS_STRATEGY_NAME
    assert resolution.basis == "detection"


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def test_build_chunks_sections_with_breadcrumbs() -> None:
    plan = MarkdownSectionsStrategy().build_chunks(MARKDOWN, "raw_text", _profile())
    texts = [chunk.text for chunk in plan.chunks]
    assert texts == [
        "# Alpha\n\nAlpha body text lives here.\n\n",
        "## Sub\n\nSub body text.\n\n",
        "# Beta\n\nBeta body text.\n",
    ]
    paths = [chunk.metadata["heading_path"] for chunk in plan.chunks]
    assert paths == [["Alpha"], ["Alpha", "Sub"], ["Beta"]]
    assert [chunk.metadata["heading_level"] for chunk in plan.chunks] == [1, 2, 1]
    assert [chunk.source_type for chunk in plan.chunks] == ["markdown", "markdown", "markdown"]
    assert validate_plan(plan) == []


def test_build_chunks_offset_invariant_holds() -> None:
    plan = MarkdownSectionsStrategy().build_chunks(MARKDOWN, "raw_text", _profile())
    for chunk in plan.chunks:
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text


def test_build_chunks_preamble_is_its_own_section() -> None:
    text = "Intro line.\n\n# Head\n\nBody\n"
    plan = MarkdownSectionsStrategy().build_chunks(text, "raw_text", _profile())
    assert len(plan.chunks) == 2
    first = plan.chunks[0]
    assert first.text == "Intro line.\n\n"
    assert first.metadata["heading_level"] == 0
    assert first.metadata["heading_path"] == []
    assert plan.chunks[1].metadata["heading_path"] == ["Head"]
    assert validate_plan(plan) == []


def test_build_chunks_setext_headings() -> None:
    text = "Title\n=====\n\nBody text.\n"
    plan = MarkdownSectionsStrategy().build_chunks(text, "raw_text", _profile())
    assert len(plan.chunks) == 1
    assert plan.chunks[0].metadata["heading_level"] == 1
    assert plan.chunks[0].metadata["heading_path"] == ["Title"]
    assert validate_plan(plan) == []


def test_build_chunks_fenced_headings_do_not_split() -> None:
    text = "```python\n# not a heading\n```\n\n# Real\n\nBody\n"
    plan = MarkdownSectionsStrategy().build_chunks(text, "raw_text", _profile())
    # The fenced block rides along as the level-0 preamble; the '# ' line
    # inside the fence must NOT become a section heading.
    paths = [chunk.metadata["heading_path"] for chunk in plan.chunks]
    assert paths == [[], ["Real"]]
    assert plan.chunks[0].text == "```python\n# not a heading\n```\n\n"
    assert validate_plan(plan) == []


def test_build_chunks_no_headings_is_empty_but_conformant() -> None:
    plan = MarkdownSectionsStrategy().build_chunks("Just prose.\n", "raw_text", _profile())
    assert plan.chunks == []
    assert plan.empty_reason == "no heading structure found"
    assert validate_plan(plan) == []


def test_build_chunks_empty_input_is_empty_but_conformant() -> None:
    plan = MarkdownSectionsStrategy().build_chunks("", "raw_text", _profile())
    assert plan.chunks == []
    assert plan.empty_reason == "no content to segment"
    assert validate_plan(plan) == []


def test_oversized_section_splits_with_exact_spans() -> None:
    body = "word " * (TOKEN_BUDGET_CHARS // 2)
    text = f"# Big\n\n{body}\n"
    plan = MarkdownSectionsStrategy().build_chunks(text, "raw_text", _profile())
    assert len(plan.chunks) > 1
    assert any("split" in warning for warning in plan.warnings)
    for chunk in plan.chunks:
        assert len(chunk.text) <= TOKEN_BUDGET_CHARS
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
    assert validate_plan(plan) == []
