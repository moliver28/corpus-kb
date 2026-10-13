"""U36: the cumulative saturation curve rendered into report surfaces.

Pins the renderer's exact output shape (coding/saturation.py:
render_saturation_curve) and its WIRE-IN: gate_measures.gate_saturation
carries the rendered curve in its value, so the release review packet (the
assess bundle / recorded gate evidence) surfaces the whole
unique-to-total trajectory beside the final scalar ISR it already had.
"""

from __future__ import annotations

import pytest

from corpus_kb.coding.saturation import (
    _RENDER_MAX_POINTS,
    isr,
    render_saturation_curve,
    saturation_curve,
)
from corpus_kb.research.release.gate_measures import gate_saturation
from corpus_kb.research.release.gate_types import GateInputs
from corpus_kb.research.release.profiles import resolve_profile

THREE_UNITS = [["a"], ["a", "b"], ["b", "c"]]

# 1/1=1.00, 2/3=0.67, 3/5=0.60 — columns right-justified to width 7.
EXPECTED_THREE_UNIT_RENDER = (
    "saturation curve (cumulative unique/total by unit index)\n"
    "   unit:       1       2       3\n"
    " unique:       1       2       3\n"
    "  total:       1       3       5\n"
    "  ratio:    1.00    0.67    0.60"
)


class TestRenderShape:
    """The renderer's output shape, byte-for-byte."""

    def test_exact_small_curve(self):
        curve = saturation_curve(THREE_UNITS)
        assert render_saturation_curve(curve) == EXPECTED_THREE_UNIT_RENDER

    def test_deterministic(self):
        curve = saturation_curve(THREE_UNITS)
        assert render_saturation_curve(curve) == render_saturation_curve(curve)

    def test_row_labels_and_header(self):
        rendered = render_saturation_curve(saturation_curve(THREE_UNITS))
        lines = rendered.split("\n")
        assert len(lines) == 5
        assert lines[1].startswith("   unit: ")
        assert lines[2].startswith(" unique: ")
        assert lines[3].startswith("  total: ")
        assert lines[4].startswith("  ratio: ")

    def test_final_column_is_whole_run_isr(self):
        curve = saturation_curve(THREE_UNITS)
        rendered = render_saturation_curve(curve)
        ratio_row = rendered.split("\n")[4]
        final_cell = ratio_row.split()[-1]
        assert final_cell == f"{isr(3, 5):.2f}"
        assert final_cell == "0.60"

    def test_empty_curve_renders_nothing(self):
        assert render_saturation_curve(saturation_curve([])) == ""

    def test_elision_beyond_max_points(self):
        curve = saturation_curve([["c1", "c2"]] * 50)
        rendered = render_saturation_curve(curve, max_points=8)
        assert "..." in rendered
        lines = rendered.split("\n")
        unit_row = lines[1].split(": ", 1)[1].split()
        # First and last points always render; the middle is one elision cell.
        assert unit_row[0] == "1"
        assert unit_row[-1] == "50"
        assert unit_row.count("...") == 1
        # max_points cells: 7 real + 1 elision marker + the final point.
        assert len(unit_row) == 9

    def test_default_max_points_is_the_module_constant(self):
        curve = saturation_curve([["c1", "c2"]] * (_RENDER_MAX_POINTS + 10))
        assert "..." in render_saturation_curve(curve)  # 42 points > 32: elided
        exact = saturation_curve([["c1"]] * _RENDER_MAX_POINTS)
        assert render_saturation_curve(exact).count("...") == 0


class TestReviewPacketWireIn:
    """gate_saturation renders the curve into the release review packet."""

    def _inputs(self) -> GateInputs:
        return GateInputs(
            metrics={
                "saturation": {
                    "new_codes": 2,
                    "base_unique": 80,
                    "unit_code_sets": THREE_UNITS,
                }
            }
        )

    def test_gate_value_carries_rendered_curve(self):
        result = gate_saturation(self._inputs(), resolve_profile("team-codebook"))
        value = result.value
        assert isinstance(value, dict)
        assert value["curve"] == EXPECTED_THREE_UNIT_RENDER
        # The scalar it used to be rides beside it, consistent with the curve.
        assert value["curve_final_isr"] == pytest.approx(0.6)
        assert value["isr"] == pytest.approx(0.6)

    def test_gate_without_unit_sets_has_no_curve(self):
        result = gate_saturation(
            GateInputs(metrics={"saturation": {"new_codes": 2, "base_unique": 80}}),
            resolve_profile("team-codebook"),
        )
        value = result.value
        assert isinstance(value, dict)
        assert "curve" not in value

    def test_assess_bundle_surfaces_the_curve(self):
        from corpus_kb.research.release.gates import assess

        bundle = assess(self._inputs(), resolve_profile("team-codebook"))
        saturation_rows = [g for g in bundle["gate_results"] if g["gate_id"] == "saturation"]
        assert len(saturation_rows) == 1
        value = saturation_rows[0]["value"]
        assert isinstance(value, dict)
        assert "saturation curve" in str(value["curve"])
