"""Tier-3 cascade wiring tests (issue #132, requires_postgres).

``run_deductive`` is the ONE eval path. It always computes the combined
review priority and the closed-enum routing reasons, and with
``coding.uncertainty.tier3_enabled`` it escalates the flagged minority,
measures semantic entropy, and records tier-3 signals on the assignment
events. The evidence view pins that run and surfaces the disagreement flag
and the review ranking. The live model seams are monkeypatched here, so
these tests never touch Ollama.
"""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from corpus_kb.coding.uncertainty.review_priority import REVIEW_REASONS
from corpus_kb.projections.catchup import advance_all_projections
from corpus_kb.research.deductive_run import run_deductive
from corpus_kb.research.notebook_views import evidence_for_code
from tests.test_notebook_db import _seed, _seed_codebook

pytestmark = pytest.mark.requires_postgres

WIRING_TENANT = "00000000-0000-0000-0000-000000000005"
TENANT_A = "00000000-0000-0000-0000-000000000006"
TENANT_B = "00000000-0000-0000-0000-000000000007"

OFF_CFG = {"research": {"release": {"enforcement": "off"}}}
TIER3_CFG = {
    "research": {"release": {"enforcement": "off"}},
    "coding": {
        "uncertainty": {
            "tier3_enabled": True,
            "model": "qwen3:4b",
            "base_url": "http://localhost:11434",
        }
    },
}


def _two_cluster_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic resamples + an NLI judge with two entailment clusters."""
    import corpus_kb.research.deductive_tier3 as tier3_module

    async def fake_resample(
        unit_text: str, code_name: str, definition: str, n: int, model: str, base_url: str
    ) -> list[str]:
        return [f"sample {i}" for i in range(n)]

    async def fake_nli(a: str, b: str, model: str = "qwen3", base_url: str = "x") -> bool | None:
        ia, ib = int(a.split()[-1]), int(b.split()[-1])
        return ia // 2 == ib // 2

    monkeypatch.setattr(tier3_module, "resample_rationales", fake_resample)
    monkeypatch.setattr(tier3_module, "nli_mutual_entailment", fake_nli)


async def _seeded_tenant(pool: Any, tenant_id: str) -> dict[str, Any]:
    project = str(uuid4())
    seed = await _seed(pool, tenant_id, project)
    codes = await _seed_codebook(pool, tenant_id, seed)
    return {"project": project, "seed": seed, "codes": codes}


async def _checkpoint_of(pool: Any, tenant_id: str, run_id: str) -> dict[str, Any]:
    """The run checkpoint, read under the tenant GUC (FORCE RLS on all tables)."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, UUID(tenant_id)) as conn:
        row = await conn.fetchrow(
            "SELECT checkpoint FROM research_runs WHERE run_id = $1 AND tenant_id = $2",
            run_id,
            tenant_id,
        )
    assert row is not None and row["checkpoint"] is not None, "run checkpoint missing"
    raw = row["checkpoint"]
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


async def _tier3_rows(pool: Any, tenant_id: str) -> list[dict[str, Any]]:
    """Tier-3 signal rows for one tenant, read under the tenant GUC."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, UUID(tenant_id)) as conn:
        rows = await conn.fetch(
            "SELECT unit_id, semantic_entropy FROM research_signals "
            "WHERE tenant_id = $1 AND tier = 3 ORDER BY unit_id",
            tenant_id,
        )
    return [dict(r) for r in rows]


async def _tenant_unit_ids(pool: Any, tenant_id: str) -> set[int]:
    """The tenant's own research_units ids, read under the tenant GUC."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, UUID(tenant_id)) as conn:
        rows = await conn.fetch(
            "SELECT unit_id FROM research_units WHERE tenant_id = $1", tenant_id
        )
    return {int(r["unit_id"]) for r in rows}


async def test_run_records_review_priority_block(research_pool) -> None:
    """Every deductive run records the ranked review-priority block."""
    data = await _seeded_tenant(research_pool, WIRING_TENANT)
    summary = await run_deductive(
        research_pool,
        UUID(WIRING_TENANT),
        UUID(data["codes"]["version_id"]),
        project_id=UUID(data["project"]),
        cfg=OFF_CFG,
    )
    assert summary["status"] == "success"
    await advance_all_projections(research_pool, UUID(WIRING_TENANT))
    checkpoint = await _checkpoint_of(research_pool, WIRING_TENANT, summary["run_id"])
    block = checkpoint.get("review_priority")
    assert block, "the run must record the review-priority block"
    assert block["tier3_enabled"] is False
    assert block["n_escalated"] == 0
    assert block["weights"] == "equal"
    units = cast("list[dict[str, Any]]", block["units"])
    assert units, "ranked units must be present"
    priorities = [float(u["priority"]) for u in units]
    assert priorities == sorted(priorities, reverse=True), "units must rank by priority"
    for unit in units:
        assert unit["reason"] in REVIEW_REASONS
        assert unit["escalated"] is False
        assert unit["semantic_entropy"] is None
    n_tier3 = len(await _tier3_rows(research_pool, WIRING_TENANT))
    assert n_tier3 == 0, "tier-3 disabled must not fabricate semantic-entropy rows"


async def test_run_tier3_enabled_records_signals_and_evidence_flags(
    research_pool, monkeypatch
) -> None:
    """The enabled escalation measures SE, records tier-3 rows, and the view flags them."""
    _two_cluster_fakes(monkeypatch)
    data = await _seeded_tenant(research_pool, WIRING_TENANT)
    summary = await run_deductive(
        research_pool,
        UUID(WIRING_TENANT),
        UUID(data["codes"]["version_id"]),
        project_id=UUID(data["project"]),
        cfg=TIER3_CFG,
    )
    assert summary["status"] == "success"
    await advance_all_projections(research_pool, UUID(WIRING_TENANT))
    checkpoint = await _checkpoint_of(research_pool, WIRING_TENANT, summary["run_id"])
    block = checkpoint["review_priority"]
    assert block["tier3_enabled"] is True
    assert block["n_escalated"] >= 1, "the flagged minority must escalate"
    assert block["n_nli_calls"] == 10 * block["n_escalated"]
    escalated = [
        int(u["unit_id"])
        for u in cast("list[dict[str, Any]]", block["units"])
        if bool(u["escalated"])
    ]
    by_unit = {
        int(r["unit_id"]): r["semantic_entropy"]
        for r in await _tier3_rows(research_pool, WIRING_TENANT)
    }
    for unit_id in escalated:
        assert by_unit.get(unit_id) is not None, "escalated unit must carry a signal row"
        assert float(by_unit[unit_id]) > 0.0, "two entailment clusters must give positive SE"

    result = await evidence_for_code(research_pool, UUID(WIRING_TENANT), "billing-migration")
    assert result["status"] == "ok"
    for tab in cast("dict[str, list[dict[str, Any]]]", result["tabs"]).values():
        for entry in tab:
            assert "review_priority" in entry, "the pinned run's ranking must surface"
            assert entry["review_reason"] in REVIEW_REASONS
            if int(entry["unit_id"]) in escalated:
                assert entry["disagreement"] is True
                assert 0.0 <= float(entry["review_priority"]) <= 1.0


async def test_evidence_view_is_two_tenant_safe(research_pool) -> None:
    """The tier-3 evidence path returns zero cross-tenant rows (RLS respected)."""
    from tests.research_db import RLS_PROBE_ROLE

    seeded: dict[str, dict[str, Any]] = {}
    for tenant in (TENANT_A, TENANT_B):
        data = await _seeded_tenant(research_pool, tenant)
        seeded[tenant] = data
    # Give each tenant its own tier-3 signal, run checkpoint, and assignment
    # shape; only the code NAME is shared between the tenants.
    for tenant, data in seeded.items():
        unit_a = data["seed"]["units"]["A"]
        async with research_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant)
            await conn.execute(
                """
                INSERT INTO research_signals (tenant_id, unit_id, tier, semantic_entropy)
                VALUES ($1, $2, 3, 0.9)
                """,
                tenant,
                unit_a,
            )
        result = await evidence_for_code(research_pool, UUID(tenant), "billing-migration")
        assert result["status"] == "empty", "no assignments yet: the view stays empty"

    # Assign one unit per tenant under its own version, re-check isolation.
    from tests.test_notebook_db import _assign

    for tenant, data in seeded.items():
        await _assign(
            research_pool,
            tenant,
            data["seed"]["units"]["A"],
            data["codes"]["A"],
            data["codes"]["version_id"],
        )
    for tenant in seeded:
        result = await evidence_for_code(research_pool, UUID(tenant), "billing-migration")
        assert result["status"] == "ok"
        units = [
            int(e["unit_id"])
            for tab in cast("dict[str, list[dict[str, Any]]]", result["tabs"]).values()
            for e in tab
        ]
        assert units, "each tenant sees its own assigned unit"
        own_ids = await _tenant_unit_ids(research_pool, tenant)
        assert set(units) <= own_ids, (
            "the view must return only units that belong to the calling tenant"
        )

    # RLS negative leg: a plain non-owner role without the tenant GUC sees
    # nothing, so the view cannot leak even if a tenant filter were dropped.
    conn = await research_pool.acquire()
    try:
        await conn.execute(f"SET ROLE {RLS_PROBE_ROLE}")
        blocked = await conn.fetchval(
            "SELECT count(*) FROM research_signals WHERE tenant_id = ANY($1::uuid[])",
            [TENANT_A, TENANT_B],
        )
        await conn.execute("RESET ROLE")
    finally:
        await research_pool.release(conn)
    assert blocked == 0, "the probe role must see zero signal rows without a tenant GUC"
