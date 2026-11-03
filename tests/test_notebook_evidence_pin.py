"""Version-pinned evidence view tests (issue #132, requires_postgres).

The evidence view must pin ONE codebook version and ONE run: the newest
run with rows for the resolved version. Re-runs replace the view instead
of appending, and rows from a sibling version never mix in. Legacy rows
(no version tag, no run) surface only while nothing newer exists.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from corpus_kb.research.notebook_views import evidence_for_code
from tests.test_notebook_db import _assign, _seed

pytestmark = pytest.mark.requires_postgres

# Dedicated tenant: the session DB is shared across the research fixtures.
PIN_TENANT = "00000000-0000-0000-0000-000000000004"


async def _seed_version(pool: Any, tenant_id: str, label: str, codes: list[tuple[str, str]]) -> str:
    """One codebook version; codes are (code_id, name) pairs with no gold."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    version_id = str(uuid4())
    async with tenant_connection(pool, tenant_id) as conn:
        await conn.execute(
            """
            INSERT INTO codebook_versions (version_id, tenant_id, label, sha256, paradigm)
            VALUES ($1, $2, $3, $4, 'deductive')
            """,
            version_id,
            tenant_id,
            label,
            uuid4().hex,
        )
        for code_id, name in codes:
            await conn.execute(
                """
                INSERT INTO code_registry
                (code_id, tenant_id, codebook_version_id, name, brief_definition,
                 inclusion_criteria, exclusion_criteria, theory)
                VALUES ($1, $2, $3, $4, $5, 'inclusion', 'exclusion', '{}'::jsonb)
                """,
                code_id,
                tenant_id,
                version_id,
                name,
                f"definition for {name}",
            )
    return version_id


async def _seed_run(
    pool: Any, tenant_id: str, run_id: str, started_at: datetime, checkpoint: dict[str, Any]
) -> None:
    """One research_runs row with an explicit start time and checkpoint."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        await conn.execute(
            """
            INSERT INTO research_runs (run_id, tenant_id, state, checkpoint, started_at)
            VALUES ($1, $2, 'stopped', $3::jsonb, $4)
            """,
            run_id,
            tenant_id,
            json.dumps(checkpoint),
            started_at,
        )


async def _assign_run(
    pool: Any,
    tenant_id: str,
    unit_id: int,
    code_id: str,
    version_id: str,
    run_id: str,
) -> int:
    """One assignment row tagged with its codebook version AND run."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        return int(
            await conn.fetchval(
                """
                INSERT INTO research_assignments
                (tenant_id, assignment_aggregate_id, unit_id, code_id, cb_version_id,
                 run_id, sim_answer, evidence_basis, rationale, confidence, status)
                VALUES ($1, $2, $3, $4, $5, $6, 0.95, 'explicit_in_answer',
                        'deductive:v2:explicit', 'high', 'auto')
                RETURNING assignment_id
                """,
                tenant_id,
                str(uuid4()),
                unit_id,
                code_id,
                version_id,
                run_id,
            )
        )


def _entry_units(result: dict[str, Any]) -> list[int]:
    return [
        int(e["unit_id"])
        for tab in cast("dict[str, list[dict[str, Any]]]", result["tabs"]).values()
        for e in tab
    ]


async def test_evidence_view_pins_the_resolved_codebook_version(research_pool) -> None:
    """Rows from a sibling version with the same code id must not mix in.

    The schema primary key (code_id, codebook_version_id, tenant_id) makes a
    shared code id across versions a first-class shape. The view resolves the
    code against the newest version and must read THAT version only.
    """
    project = str(uuid4())
    seed = await _seed(research_pool, PIN_TENANT, project)
    code_id = str(uuid4())
    v1 = await _seed_version(research_pool, PIN_TENANT, "pin-v1", [(code_id, "pin-code")])
    v2 = await _seed_version(research_pool, PIN_TENANT, "pin-v2", [(code_id, "pin-code")])
    await _assign(research_pool, PIN_TENANT, seed["units"]["A"], code_id, v1)
    await _assign(research_pool, PIN_TENANT, seed["units"]["A2"], code_id, v1)
    await _assign(research_pool, PIN_TENANT, seed["units"]["B"], code_id, v2)

    result = await evidence_for_code(research_pool, UUID(PIN_TENANT), "pin-code")
    assert result["status"] == "ok"
    assert result["n_units"] == 1, "sibling-version rows must not inflate the count"
    assert _entry_units(result) == [seed["units"]["B"]]


async def test_evidence_view_replace_on_rerun(research_pool) -> None:
    """A newer run for the same version replaces the view, row for row.

    Run R1 codes two units; the rerun R2 codes one. The view must show only
    R2's rows and read conformal sizes from R2's checkpoint only.
    """
    project = str(uuid4())
    seed = await _seed(research_pool, PIN_TENANT, project)
    code_id = str(uuid4())
    version_id = await _seed_version(
        research_pool, PIN_TENANT, "pin-run", [(code_id, "pin-run-code")]
    )
    run1 = str(uuid4())
    run2 = str(uuid4())
    await _seed_run(
        research_pool,
        PIN_TENANT,
        run1,
        datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        {"conformal": {"per_unit": {str(seed["units"]["A"]): 3}}},
    )
    await _seed_run(
        research_pool,
        PIN_TENANT,
        run2,
        datetime(2026, 1, 2, 10, 0, 0, tzinfo=UTC),
        {"conformal": {"per_unit": {str(seed["units"]["B"]): 2}}},
    )
    await _assign_run(research_pool, PIN_TENANT, seed["units"]["A"], code_id, version_id, run1)
    await _assign_run(research_pool, PIN_TENANT, seed["units"]["A2"], code_id, version_id, run1)
    await _assign_run(research_pool, PIN_TENANT, seed["units"]["B"], code_id, version_id, run2)

    result = await evidence_for_code(research_pool, UUID(PIN_TENANT), "pin-run-code")
    assert result["status"] == "ok"
    assert result["n_units"] == 1, "the rerun must replace the stale run's rows"
    assert _entry_units(result) == [seed["units"]["B"]]
    by_unit = {
        int(e["unit_id"]): e
        for tab in cast("dict[str, list[dict[str, Any]]]", result["tabs"]).values()
        for e in tab
    }
    assert by_unit[seed["units"]["B"]]["conformal_set_size"] == 2


async def test_evidence_view_legacy_rows_without_run_still_surface(research_pool) -> None:
    """Guard: pre-versioning rows (no run, no version tag) stay visible.

    The pin must never hide the only data a tenant has. This seed has no
    research_runs rows at all, so the legacy rows are the view's whole world.
    """
    project = str(uuid4())
    seed = await _seed(research_pool, PIN_TENANT, project)
    code_id = str(uuid4())
    await _seed_version(research_pool, PIN_TENANT, "pin-legacy", [(code_id, "pin-legacy-code")])
    await _assign(research_pool, PIN_TENANT, seed["units"]["A"], code_id, None)
    await _assign(research_pool, PIN_TENANT, seed["units"]["A2"], code_id, None)

    result = await evidence_for_code(research_pool, UUID(PIN_TENANT), "pin-legacy-code")
    assert result["status"] == "ok"
    assert sorted(_entry_units(result)) == sorted([seed["units"]["A"], seed["units"]["A2"]])
