"""Tests for the vendored Krippendorff-alpha reliability module.

Ported from codebook-plugin/lib/tests/test_reliability.py with additional
cross-validation against the maintained krippendorff package.
"""

from __future__ import annotations

import pytest

from corpus_kb.coding.reliability import (
    _alpha_from_units,
    _kappa_from_units,
    computeReliability,
)


def _assignment(code_id):
    return {"code_id": code_id, "quote": "q", "rationale": "r", "confidence": "high"}


def _entry(segment_id, applied):
    return {
        "segment_id": segment_id,
        "assignments": [_assignment("L01")] if applied else [],
    }


class TestAlphaHandComputedFixture:
    """Worked example, computed by hand against the exact plan formula:

        alpha_c = 1 - (n - 1) * sum_u[ n_u0*n_u1 / (m_u - 1) ] / (n_0 * n_1)

    Two segments (units), 3 coders each:
        s1: votes [1, 1, 0]  -> n_u0=1, n_u1=2, m_u=3
        s2: votes [1, 0, 0]  -> n_u0=2, n_u1=1, m_u=3

    n = 3 + 3 = 6
    n_0 = 1 + 2 = 3
    n_1 = 2 + 1 = 3
    sum_u[n_u0*n_u1/(m_u-1)] = (1*2)/2 + (2*1)/2 = 1 + 1 = 2
    alpha = 1 - (6-1)*2 / (3*3) = 1 - 10/9 = -1/9 = -0.111...
    """

    def setup_method(self):
        self.sheets = [
            {
                "coder_id": "c1",
                "entries": [_entry("s1", True), _entry("s2", True)],
            },
            {
                "coder_id": "c2",
                "entries": [_entry("s1", True), _entry("s2", False)],
            },
            {
                "coder_id": "c3",
                "entries": [_entry("s1", False), _entry("s2", False)],
            },
        ]
        self.segments = [
            {"segment_id": "s1", "text": "segment one text"},
            {"segment_id": "s2", "text": "segment two text"},
        ]

    def test_alpha_matches_hand_computed_value(self):
        result = computeReliability(self.sheets, self.segments)
        alpha = result["per_code"]["L01"]["alpha"]
        assert alpha is not None
        assert abs(alpha - (-1.0 / 9.0)) < 1e-9
        assert result["per_code"]["L01"]["n"] == 6
        assert result["per_code"]["L01"]["apps"] == 3

    def test_unstable_flag_under_5_apps(self):
        result = computeReliability(self.sheets, self.segments)
        assert result["per_code"]["L01"]["unstable"] is True
        assert "unstable:L01" in result["flags"]

    def test_overall_equals_per_code_when_only_one_code_present(self):
        result = computeReliability(self.sheets, self.segments)
        assert abs(result["overall"]["alpha"] - result["per_code"]["L01"]["alpha"]) < 1e-9


class TestNoVarianceIsNull:
    def test_all_coders_agree_every_time_is_null(self):
        sheets = [
            {
                "coder_id": "c1",
                "entries": [
                    {"segment_id": "s1", "assignments": [_assignment("L02")]},
                    {"segment_id": "s2", "assignments": [_assignment("L02")]},
                ],
            },
            {
                "coder_id": "c2",
                "entries": [
                    {"segment_id": "s1", "assignments": [_assignment("L02")]},
                    {"segment_id": "s2", "assignments": [_assignment("L02")]},
                ],
            },
            {
                "coder_id": "c3",
                "entries": [
                    {"segment_id": "s1", "assignments": [_assignment("L02")]},
                    {"segment_id": "s2", "assignments": [_assignment("L02")]},
                ],
            },
        ]
        segments = [
            {"segment_id": "s1", "text": "t1"},
            {"segment_id": "s2", "text": "t2"},
        ]
        result = computeReliability(sheets, segments)
        assert result["per_code"]["L02"]["alpha"] is None
        assert result["per_code"]["L02"]["kappa"] is None


class TestApplicationsAt5OrMoreAreStable:
    def test_five_apps_is_not_unstable(self):
        sheets_c1_entries = []
        sheets_c2_entries = []
        for i in range(5):
            sid = f"s{i}"
            sheets_c1_entries.append({"segment_id": sid, "assignments": [_assignment("L03")]})
            sheets_c2_entries.append({"segment_id": sid, "assignments": []})
        sheets = [
            {"coder_id": "c1", "entries": sheets_c1_entries},
            {"coder_id": "c2", "entries": sheets_c2_entries},
        ]
        segments = [{"segment_id": f"s{i}", "text": "t"} for i in range(5)]
        result = computeReliability(sheets, segments)
        assert result["per_code"]["L03"]["apps"] == 5
        assert result["per_code"]["L03"]["unstable"] is False
        assert result["flags"] == []


class TestExcludedSegmentsAreIgnored:
    def test_excluded_segments_do_not_count_as_units(self):
        sheets = [
            {
                "coder_id": "c1",
                "entries": [
                    {"segment_id": "s1", "assignments": [_assignment("L04")]},
                    {"segment_id": "sub1", "assignments": [_assignment("L04")]},
                ],
            },
            {
                "coder_id": "c2",
                "entries": [
                    {"segment_id": "s1", "assignments": []},
                    {"segment_id": "sub1", "assignments": []},
                ],
            },
        ]
        segments = [
            {"segment_id": "s1", "text": "t1"},
            {"segment_id": "sub1", "text": "ok", "excluded": "sub_minimal"},
        ]
        result = computeReliability(sheets, segments)
        assert result["per_code"]["L04"]["n"] == 2


class TestMissingSheetEntryIsMissingDataNotZero:
    def test_coder_with_no_entry_for_a_segment_does_not_lower_m_u(self):
        sheets = [
            {
                "coder_id": "c1",
                "entries": [
                    {"segment_id": "s1", "assignments": [_assignment("L05")]},
                    {"segment_id": "s2", "assignments": [_assignment("L05")]},
                ],
            },
            {
                "coder_id": "c2",
                "entries": [
                    {"segment_id": "s1", "assignments": []},
                    {"segment_id": "s2", "assignments": []},
                ],
            },
            {
                "coder_id": "c3",
                "entries": [
                    {"segment_id": "s1", "assignments": []},
                    # no entry at all for s2
                ],
            },
        ]
        segments = [
            {"segment_id": "s1", "text": "t1"},
            {"segment_id": "s2", "text": "t2"},
        ]
        result = computeReliability(sheets, segments)
        assert result["per_code"]["L05"]["n"] == 5


class TestPooledAlphaArithmetic:
    """Directly verifies the pooling arithmetic used for the overall alpha:
    pooling is just applying the same coincidence-matrix formula to the
    union of every code's per-segment units.

    Units: L01's two units from the hand-computed fixture above, plus 5
    units of (n_u0=1, n_u1=1, m_u=2) from a second code.

    n = (3+3) + 5*2 = 16
    n_0 = (1+2) + 5*1 = 8
    n_1 = (2+1) + 5*1 = 8
    sum_u[...] = (1*2/2 + 2*1/2) + 5*(1*1/1) = 2 + 5 = 7
    alpha = 1 - (16-1)*7 / (8*8) = 1 - 105/64 = -0.640625
    """

    def test_pooled_alpha_matches_hand_computation(self):
        units = [(1, 2, 3), (2, 1, 3)] + [(1, 1, 2)] * 5
        alpha, n, n_0, n_1 = _alpha_from_units(units)
        assert n == 16
        assert n_0 == 8
        assert n_1 == 8
        assert abs(alpha - (-0.640625)) < 1e-9


class TestFleissKappaSecondary:
    def test_kappa_runs_and_returns_a_float_for_normal_data(self):
        units = [(1, 2, 3), (2, 1, 3)]
        kappa = _kappa_from_units(units)
        assert isinstance(kappa, float)

    def test_kappa_is_none_for_empty_units(self):
        assert _kappa_from_units([]) is None


class TestMultiLabelGuard:
    def test_multi_label_input_raises_valueerror(self):
        """Multi-label (multiple assignments of same code per segment) is
        rejected at entry time per M1 constraint."""
        sheets = [
            {
                "coder_id": "c1",
                "entries": [
                    {
                        "segment_id": "s1",
                        "assignments": [
                            _assignment("L06"),
                            _assignment("L06"),  # duplicate code
                        ],
                    },
                ],
            },
        ]
        with pytest.raises(ValueError, match="Multi-label input"):
            computeReliability(sheets)


@pytest.mark.requires_krippendorff
class TestCrossValidationWithKrippendorff:
    """Independent cross-check: verify the vendored alpha matches the
    maintained krippendorff package (binary, nominal) on a fixed toy dataset,
    confirming parity against a second implementation."""

    def test_vendored_alpha_matches_krippendorff_package(self):
        krippendorff = pytest.importorskip("krippendorff")

        sheets = [
            {
                "coder_id": "c1",
                "entries": [
                    {"segment_id": "s1", "assignments": [_assignment("TARGET")]},
                    {"segment_id": "s2", "assignments": [_assignment("TARGET")]},
                    {"segment_id": "s3", "assignments": []},
                    {"segment_id": "s4", "assignments": [_assignment("TARGET")]},
                ],
            },
            {
                "coder_id": "c2",
                "entries": [
                    {"segment_id": "s1", "assignments": [_assignment("TARGET")]},
                    {"segment_id": "s2", "assignments": []},
                    {"segment_id": "s3", "assignments": []},
                    {"segment_id": "s4", "assignments": [_assignment("TARGET")]},
                ],
            },
        ]
        segments = [
            {"segment_id": "s1"},
            {"segment_id": "s2"},
            {"segment_id": "s3"},
            {"segment_id": "s4"},
        ]

        # Our vendored implementation
        our_result = computeReliability(sheets, segments)
        our_alpha = our_result["per_code"]["TARGET"]["alpha"]

        # Krippendorff package (binary, nominal scale)
        # Input format: list of lists where each inner list is one unit's
        # per-coder values.
        data = [
            [1, 1],  # s1: both coders applied
            [1, 0],  # s2: c1 applied, c2 didn't
            [0, 0],  # s3: neither applied
            [1, 1],  # s4: both applied
        ]
        kripp_alpha = krippendorff.alpha(data, level_of_measurement="nominal")

        # Must match to 6 decimals
        assert our_alpha is not None
        assert abs(our_alpha - kripp_alpha) < 1e-6, (
            f"Mismatch: ours={our_alpha}, krippendorff={kripp_alpha}"
        )
