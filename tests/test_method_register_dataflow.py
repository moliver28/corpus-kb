"""Golden v1 ladder fixture and the derived delta map for the dataflow ladder.

The freeze constants pin the hand-authored v1 links (register 3.2.0,
issue 431). The derived tables are the reviewed acceptance columns from
the derived-ladder plan (issue 439). Derivation, journey, and route
tests import these constants.
"""

from __future__ import annotations

from corpus_kb.analysis.method_register_loader import load_method_register

ALL_IDS = [f"{index:02d}" for index in range(1, 31)]

# v1 hand-authored links (register 3.2.0), frozen from master.
GOLDEN_PREREQS: dict[str, tuple[str, ...]] = {mid: () for mid in ALL_IDS}
GOLDEN_PREREQS.update(
    {
        "13": ("04",),
        "14": ("04",),
        "15": ("04",),
        "20": ("16",),
    }
)

GOLDEN_NEXT: dict[str, tuple[str, ...]] = {mid: () for mid in ALL_IDS}
GOLDEN_NEXT.update(
    {
        "02": ("03",),
        "04": ("13", "14", "15"),
        "07": ("06",),
        "09": ("10", "11"),
        "12": ("04",),
        "13": ("14",),
        "15": ("16", "17"),
        "16": ("17", "20"),
        "18": ("04",),
        "19": ("16",),
        "23": ("28",),
    }
)

# Reviewed derived ladder: the acceptance columns of the plan's delta table.
EXPECTED_DERIVED_PREREQS: dict[str, tuple[str, ...]] = {mid: () for mid in ALL_IDS}
EXPECTED_DERIVED_PREREQS.update(
    {
        "03": ("02",),
        "04": ("12", "18"),
        "06": ("07",),
        "10": ("09",),
        "11": ("04", "09"),
        "13": ("04",),
        "14": ("04", "13"),
        "15": ("04",),
        "16": ("04", "15", "19"),
        "17": ("04", "15", "16"),
        "20": ("16", "17"),
        "27": ("04",),
        "28": ("23",),
    }
)

EXPECTED_DERIVED_NEXT: dict[str, tuple[str, ...]] = {mid: () for mid in ALL_IDS}
EXPECTED_DERIVED_NEXT.update(
    {
        "02": ("03",),
        "04": ("11", "13", "14", "15", "16", "17", "27"),
        "07": ("06",),
        "09": ("10", "11"),
        "12": ("04",),
        "13": ("14",),
        "15": ("16", "17"),
        "16": ("17", "20"),
        "17": ("20",),
        "18": ("04",),
        "19": ("16",),
        "23": ("28",),
    }
)


def test_golden_tables_cover_all_30_ids() -> None:
    for table in (GOLDEN_PREREQS, GOLDEN_NEXT, EXPECTED_DERIVED_PREREQS, EXPECTED_DERIVED_NEXT):
        assert list(table) == ALL_IDS


def test_golden_tables_match_the_live_v1_register() -> None:
    """The freeze captures v1, so this passes on master before any edit."""
    register = load_method_register({})
    by_id = {row.id: row for row in register.methods}
    for mid, row in by_id.items():
        assert list(row.prerequisites or []) == list(GOLDEN_PREREQS[mid])
        assert list(row.next or []) == list(GOLDEN_NEXT[mid])
