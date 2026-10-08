"""The AC1 review-trail triples must count BOTH raters (model + human).

The pre-fix encoding emitted (0, 1, 2) for accepts and (1, 0, 2) for
overrides - both contribute zero agreeing pairs in gwet_ac1's (n_u0, n_u1,
m_u) triples, forcing observed agreement Pa to 0 and AC1 <= 0 for every
nonempty review trail. Accepts are (0, 2, 2) (both raters applied) and
overrides are (1, 1, 2) (model applied, human ruled 0) - the convention
pinned by tests/test_irr_governance.py.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.research.irr_governance import gwet_ac1
from corpus_kb.research.report_irr_sheets import load_irr_sheets

TENANT = UUID(int=11)
VERSION = UUID(int=12)


class _FakeConn:
    """Dispatches fetch() by SQL shape: review trail vs assignment summary."""

    def __init__(self, review_rows: list[dict[str, Any]], assignment_rows: list[dict[str, Any]]):
        self.review_rows = review_rows
        self.assignment_rows = assignment_rows

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        if "research_reviews" in sql:
            return self.review_rows
        return self.assignment_rows


def _assignment(unit_id: int, code_id: str) -> dict[str, Any]:
    """One model assignment row with the keys load_assignment_rows converts."""
    return {
        "unit_id": unit_id,
        "code_id": code_id,
        "status": "review",
        "evidence_basis": None,
        "rationale": "routed for review",
        "sim_answer": 0.5,
        "sim_qa": 0.5,
        "source_type": "answer",
    }


async def test_review_trail_triples_count_both_raters() -> None:
    conn = _FakeConn(
        review_rows=[
            {"unit_id": 1, "code_id": "c1", "decision": "override"},
            {"unit_id": 2, "code_id": "c1", "decision": "accept"},
        ],
        assignment_rows=[
            # The model applied c1 on both units (that is why both routed).
            _assignment(1, "c1"),
            _assignment(2, "c1"),
        ],
    )
    _reliability, units_by_code = await load_irr_sheets(conn, TENANT, VERSION, ["c1"])
    assert units_by_code["c1"] == [(1, 1, 2), (0, 2, 2)]


async def test_all_accepts_reach_perfect_observed_agreement() -> None:
    conn = _FakeConn(
        review_rows=[{"unit_id": u, "code_id": "c1", "decision": "accept"} for u in range(1, 5)],
        assignment_rows=[_assignment(u, "c1") for u in range(1, 5)],
    )
    _reliability, units_by_code = await load_irr_sheets(conn, TENANT, VERSION, ["c1"])
    assert units_by_code["c1"] == [(0, 2, 2)] * 4
    # Pre-fix this was mathematically <= 0 for any nonempty trail.
    assert gwet_ac1(units_by_code["c1"]) == 1.0
