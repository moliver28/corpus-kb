"""Two-rater IRR sheets from the review trail (todo 17).

Split from report_inputs.py (250-line soft limit): THIS module owns the
model-vs-human sheet construction the dual IRR section consumes; the alpha/
AC1 math stays in irr_governance.py (pure).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.coding.reliability import computeReliability
from corpus_kb.research.report_inputs import load_assignment_rows

REVIEW_SHEET_SQL = """
SELECT ra.unit_id, ra.code_id, rv.decision
FROM research_reviews rv
JOIN research_assignments ra ON ra.assignment_id = rv.assignment_id
WHERE ra.tenant_id = $1 AND ra.cb_version_id = $2
"""


async def load_irr_sheets(
    conn: asyncpg.Connection, tenant_id: UUID, version_id: UUID, code_universe: list[str]
) -> tuple[dict[str, Any], dict[str, list[tuple[int, int, int]]]]:
    """Two-rater (model vs human) sheets from the review trail + AC1 triples.

    Model rater: every assignment row is an "applied" rating. Human rater:
    accepted reviews apply the code; overridden reviews rate it 0 (empty
    entry assignments); unreviewed assignments are missing data, never 0 —
    the same missing-data rule the vendored alpha enforces.
    """
    rows = await conn.fetch(REVIEW_SHEET_SQL, str(tenant_id), str(version_id))
    overrides: dict[int, set[str]] = {}
    accepts: dict[int, set[str]] = {}
    for row in rows:
        unit_id = int(row["unit_id"])
        code_id = str(row["code_id"])
        if str(row["decision"]) == "override":
            overrides.setdefault(unit_id, set()).add(code_id)
        else:
            accepts.setdefault(unit_id, set()).add(code_id)
    assignment_rows = await load_assignment_rows(conn, tenant_id, version_id)
    by_unit: dict[int, list[dict[str, Any]]] = {}
    for row in assignment_rows:
        by_unit.setdefault(row["unit_id"], []).append(row)
    model_entries: list[dict[str, Any]] = []
    human_entries: list[dict[str, Any]] = []
    units_by_code: dict[str, list[tuple[int, int, int]]] = {}
    for unit_id, rows_for_unit in sorted(by_unit.items()):
        # Re-coding (a later coding run against the same version) supersedes
        # earlier assignments: the model sheet rates each (unit, code) exactly
        # once, with the LATEST assignment (rows arrive ordered by the
        # monotonic assignment_id identity; created_at comparisons are banned
        # by house rule).
        latest: dict[str, dict[str, Any]] = {}
        for row in rows_for_unit:
            latest[row["code_id"]] = row
        model_codes = list(latest)
        model_entries.append(
            {"segment_id": unit_id, "assignments": [{"code_id": c} for c in model_codes]}
        )
        human_codes = sorted(set(accepts.get(unit_id, set())) - set(overrides.get(unit_id, set())))
        reviewed_codes = set(human_codes) | set(overrides.get(unit_id, set()))
        human_entries.append(
            {"segment_id": unit_id, "assignments": [{"code_id": c} for c in human_codes]}
        )
        for code_id in sorted(set(reviewed_codes) | set(model_codes)):
            if code_id not in reviewed_codes:
                continue
            human_applied = code_id in human_codes
            units_by_code.setdefault(code_id, []).append(
                (0 if human_applied else 1, 1 if human_applied else 0, 2)
            )
    sheets = [
        {"coder": "model", "entries": model_entries},
        {"coder": "human", "entries": human_entries},
    ]
    return computeReliability(sheets, code_universe=code_universe), units_by_code
