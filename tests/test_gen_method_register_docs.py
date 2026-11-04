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
        "assumptions": "Document boundaries stay fixed.",
        "null_calibration": "None; terms are reviewed by hand.",
        "failure_modes": "Boilerplate can outrank meaningful terms.",
        "scope_limits": "Distinctive wording only.",
        "cost_scale": "One sparse scan; CPU only.",
        "suggests": ["Which top terms look meaningless here?"],
        "evidence_citation": "https://example.org/tfidf (Documentation)",
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
    assert "  - Ask it:" in lines[6]
    assert lines[8].startswith("  - Evidence: https://example.org/tfidf")
    assert len(lines) == 9
