"""Tests for the method-register docs generator routing block.

Loads ``scripts/gen_method_register_docs.py`` the way
``tests/test_harness_pack.py`` loads the harness generator: as a loose
module, because the generator is a script, not a package module.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
GEN_PATH = ROOT / "scripts" / "gen_method_register_docs.py"

_gen_spec = importlib.util.spec_from_file_location("gen_method_register_docs_test", GEN_PATH)
assert _gen_spec is not None and _gen_spec.loader is not None
gen = importlib.util.module_from_spec(_gen_spec)
_gen_spec.loader.exec_module(gen)


def _full_method() -> dict[str, Any]:
    """One method row carrying every routing field."""
    return {
        "id": "05",
        "name": "TF-IDF keyphrase scan",
        "usage_guidance": "Inspect corpus vocabulary.",
        "validation_required": "Review the top terms.",
        "permitted_claims": "Descriptions of distinctive wording.",
        "do_not_claim": "Do not call the ranked list themes.",
        "robustness_caveat": "Sensitive to boilerplate.",
        "bias_risk": "Rare wording can rank highly.",
        "task_type": "discovery",
        "comparison_structure": "none",
        "output_form": "ranked_list",
        "evidence_basis": "lexical",
        "statistical_rigor": "descriptive",
        "cost_tier": "interactive",
        "question_phrases": ["What words come up most often across these documents?"],
        "failure_modes": "Boilerplate can outrank meaningful terms.",
        "scope_limits": "Distinctive wording only.",
        "suggests": ["Which top terms look meaningless here?"],
        "sources": ["https://example.org/tfidf (Documentation)"],
    }


def test_routing_lines_skip_rows_without_axes() -> None:
    method = _full_method()
    for name in (
        "task_type",
        "comparison_structure",
        "output_form",
        "evidence_basis",
        "statistical_rigor",
        "cost_tier",
    ):
        del method[name]
    assert gen._routing_lines(method) == []


def test_routing_lines_error_names_method_on_partial_axes() -> None:
    method = _full_method()
    del method["cost_tier"]
    with pytest.raises(SystemExit, match="05"):
        gen._routing_lines(method)


def test_routing_lines_render_full_block() -> None:
    lines = gen._routing_lines(_full_method())
    assert lines[0].startswith("  - Routing axes: task `discovery`")
    assert "cost tier `interactive`" in lines[0]
    assert "  - Ask it:" in lines[3]
    assert lines[5].startswith("  - Evidence: https://example.org/tfidf (Documentation)")
    assert len(lines) == 6


def test_routing_lines_evidence_uses_annotated_primary_source() -> None:
    """The Evidence line renders the annotated primary source, never a bare URL."""
    method = _full_method()
    method["sources"] = ["https://example.org/tfidf (Documentation)", "https://example.org/extra"]
    lines = gen._routing_lines(method)
    assert lines[-1] == "  - Evidence: https://example.org/tfidf (Documentation)"


# ---------------------------------------------------------------------------
# Progression bullets (novice-UX program, wave 1)
# ---------------------------------------------------------------------------


def test_progression_lines_skip_rows_without_fields() -> None:
    assert gen._progression_lines(_full_method(), {"05": "TF-IDF keyphrase scan"}) == []


def test_progression_lines_render_rung_and_concepts() -> None:
    method = _full_method()
    method["difficulty"] = "novice"
    method["concepts"] = ["term_weighting", "classification"]
    lines = gen._progression_lines(method, {})
    assert lines[0] == "  - Difficulty: `novice`."
    assert lines[1] == "  - Teaches: `term_weighting`, `classification`."
    assert len(lines) == 2


def test_progression_lines_name_linked_methods() -> None:
    method = _full_method()
    method["prerequisites"] = ["04"]
    method["next"] = ["16", "17"]
    lines = gen._progression_lines(method, {"04": "Dictionary rules", "16": "Frozen head"})
    assert lines[0] == "  - Requires: `04` Dictionary rules."
    assert lines[1] == "  - Next: `16` Frozen head, `17`."


def test_progression_lines_ignore_empty_link_lists() -> None:
    """An empty prerequisites/next list means none and renders no lines."""
    method = _full_method()
    method["difficulty"] = "novice"
    method["concepts"] = ["term_weighting"]
    method["prerequisites"] = []
    method["next"] = []
    lines = gen._progression_lines(method, {})
    assert len(lines) == 2
