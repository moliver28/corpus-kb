"""Offline tests for dual-coefficient IRR governance (todo 17)."""

from __future__ import annotations

import pytest

from corpus_kb.research.irr_governance import (
    classify_band,
    govern_irr,
    gwet_ac1,
)


def test_gwet_ac1_perfect_agreement_is_one():
    # Two raters, 4 units, both always positive: Pa=1, pi=1 -> Pe=0 -> AC1=1.
    units = [(0, 2, 2)] * 4
    assert gwet_ac1(units) == pytest.approx(1.0)


def test_gwet_ac1_perfect_negative_agreement():
    units = [(2, 0, 2)] * 4
    assert gwet_ac1(units) == pytest.approx(1.0)


def test_gwet_ac1_one_full_disagreement_lowers_but_less_than_alpha():
    # 9 agreeing pairs + 1 disagreeing unit: alpha collapses to 0.0 here while
    # AC1 stays near 0.89 — exactly the prevalence paradox the dual metric
    # exists for.
    units = [(1, 1, 2)] + [(0, 2, 2)] * 9
    ac1 = gwet_ac1(units)
    assert ac1 is not None
    assert ac1 > 0.85
    from corpus_kb.coding.reliability import _alpha_from_units

    alpha, _n, _n0, _n1 = _alpha_from_units(units)
    assert alpha == pytest.approx(0.0, abs=1e-6)


def test_gwet_ac1_empty_is_none():
    assert gwet_ac1([]) is None


def test_classify_bands():
    assert classify_band(0.85) == "reliable"
    assert classify_band(0.80) == "reliable"
    assert classify_band(0.70) == "tentative"
    assert classify_band(0.667) == "tentative"
    assert classify_band(0.5) == "halt"
    assert classify_band(None) == "unassessed"


def test_govern_irr_bands_and_prevalence_note():
    report = {
        "per_code": {
            "solid": {"alpha": 0.90, "kappa": 0.88},
            "tentative": {"alpha": 0.72, "kappa": 0.60},
            "rare_prevalence": {"alpha": 0.55, "kappa": 0.20},
        },
        "overall": {"alpha": 0.81, "kappa": 0.79},
    }
    units = {
        "solid": [(0, 2, 2)] * 10,
        "tentative": [(0, 2, 2)] * 4 + [(1, 1, 2)] * 2,
        "rare_prevalence": [(0, 2, 2)] * 19 + [(1, 1, 2)],
    }
    result = govern_irr(report, units)
    per_code_raw = result["per_code"]
    assert isinstance(per_code_raw, list)
    per_code = [dict(v) for v in per_code_raw]
    by_code = {v["code_id"]: v for v in per_code}
    assert by_code["solid"]["band"] == "reliable"
    assert by_code["tentative"]["band"] == "tentative"
    assert by_code["rare_prevalence"]["band"] == "halt"
    assert by_code["rare_prevalence"]["prevalence_affected"] is True
    assert result["halt"] is True
    assert result["tentative_codes"] == ["tentative"]
    notes = result["prevalence_notes"]
    assert isinstance(notes, list)
    assert any("prevalence-affected" in str(note) for note in notes)


def test_govern_irr_ac1_from_alpha_fallback():
    report = {
        "per_code": {"x": {"alpha": 0.92, "kappa": 0.8}},
        "overall": {"alpha": None, "kappa": None},
    }
    result = govern_irr(report)
    per_code_raw = result["per_code"]
    assert isinstance(per_code_raw, list)
    verdict = dict(per_code_raw[0])
    assert verdict["ac1"] is not None
    assert float(verdict["ac1"]) > 0.9
    overall = dict(result["overall"])
    assert overall["band"] == "unassessed"
