"""gate_reads — pure builders (fakes, no DB) + tenant-scoped read contracts.

The DB functions are exercised through a fake ``tenant_connection`` that
mimics the RLS contract (set app.current_tenant_id inside the transaction)
and records the SQL: the assertions pin the tenant-scoping convention
without a live Postgres.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

from corpus_kb.research.release import gate_reads
from corpus_kb.research.release.gate_reads import (
    agreement_routing_evidence,
    audit_sample_evidence,
    gate_inputs_from_blocks,
    machine_profile_from_manifest,
    partitions_evidence,
)

# ---------------------------------------------------------------------------
# pure builders
# ---------------------------------------------------------------------------


def test_machine_profile_from_manifest_extracts_the_block():
    block = {"model_id": "qwen3:8b"}
    assert machine_profile_from_manifest({"machine_profile": block}) == block
    assert machine_profile_from_manifest({}) == {}


def test_partitions_evidence_carries_optional_identifiers():
    rows = [
        {"unit_id": "u1", "partition": "discovery"},
        {"unit_id": "u2", "partition": "test"},
    ]
    evidence = partitions_evidence(rows, doc_by_unit={"u1": "d9"}, participant_by_unit={"u2": "p7"})
    assert evidence["rule"] == "disjoint"
    assignments = evidence["assignments"]
    assert isinstance(assignments, list)
    assert assignments[0] == {"unit_id": "u1", "partition": "discovery", "document_id": "d9"}
    assert assignments[1] == {"unit_id": "u2", "partition": "test", "participant_id": "p7"}


def test_audit_sample_evidence_filters_to_the_audit_partition():
    rows = [
        {"unit_id": "u1", "partition": "discovery"},
        {"unit_id": "u2", "partition": "audit", "stratum": "c1|low"},
    ]
    evidence = audit_sample_evidence(rows)
    rows_out = evidence["rows"]
    assert isinstance(rows_out, list)
    assert len(rows_out) == 1 and rows_out[0]["unit_id"] == "u2"


def test_agreement_routing_evidence_scores_and_routes():
    runs = [
        {"u1": "x", "u2": "x", "u3": "x"},
        {"u1": "x", "u2": "y", "u3": "x"},
        {"u1": "x", "u2": "x", "u3": None},
    ]
    evidence = agreement_routing_evidence(runs, routed_ids=["u3"])
    units = {u["unit_id"]: u for u in evidence["units"]}
    assert units["u1"]["agreement"] == 1.0
    assert units["u2"]["agreement"] == pytest.approx(1 / 3)
    # a None label cannot agree with anything: one agreeing pair of three
    assert units["u3"]["agreement"] == pytest.approx(1 / 3)
    assert units["u3"]["routed"] is True and units["u1"]["routed"] is False


def test_agreement_routing_evidence_single_run_is_unmeasured():
    # one run block -> fewer than 2 labels per unit -> score None, never 1.0
    evidence = agreement_routing_evidence([{"u1": "x"}], [])
    units = evidence["units"]
    assert units[0]["agreement"] is None


def test_agreement_routing_evidence_is_deterministic_and_empty_safe():
    runs = [{"u1": "x"}, {"u1": "y"}]
    assert agreement_routing_evidence(runs, []) == agreement_routing_evidence(runs, [])
    assert agreement_routing_evidence([], []) == {"units": []}


def test_gate_inputs_from_blocks_maps_known_and_extra_keys():
    inputs = gate_inputs_from_blocks(
        {
            "codebook": {"categories": []},
            "reliability": {"overall": {"alpha": 0.8}},
            "custom_note": {"anything": True},
        }
    )
    assert inputs.codebook == {"categories": []}
    assert inputs.reliability == {"overall": {"alpha": 0.8}}
    assert inputs.extra == {"custom_note": {"anything": True}}
    # a known key with a non-dict value is dropped, not coerced
    assert gate_inputs_from_blocks({"heldout": "junk"}).heldout is None


def test_gate_inputs_from_blocks_merges_metrics():
    inputs = gate_inputs_from_blocks({"metrics": {"saturation": {"new_codes": 1}}})
    assert inputs.metrics == {"saturation": {"new_codes": 1}}


# ---------------------------------------------------------------------------
# tenant-scoped DB reads (fake connection; the SQL contract is the assertion)
# ---------------------------------------------------------------------------


class FakeConn:
    """Records every query + args; returns prebuilt rows."""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self.set_configs: list[tuple[object, ...]] = []

    async def fetch(self, query: str, *args: object) -> list[Any]:
        self.queries.append((query, args))
        return self.rows

    async def fetchval(self, query: str, *args: object) -> Any:
        self.queries.append((query, args))
        return self.rows[0] if self.rows else None

    async def execute(self, query: str, *args: object) -> str:
        self.set_configs.append(args)
        return "OK"

    def transaction(self) -> object:
        return _NullTx()


class _NullTx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn


def install_fake_tenant_connection(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    """Mimic tenant_connection: set the RLS tenant inside one transaction."""

    @asynccontextmanager
    async def fake_tenant_connection(pool: FakePool, tenant_id: object):
        seen.append(str(tenant_id))
        conn = pool.conn
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(tenant_id))
        yield conn

    monkeypatch.setattr("corpus_kb.storage.tenant_conn.tenant_connection", fake_tenant_connection)


@pytest.mark.asyncio
async def test_audit_rows_scopes_by_tenant_and_scope(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []
    install_fake_tenant_connection(monkeypatch, seen)
    conn = FakeConn(
        [
            {
                "scope": "s1",
                "unit_id": 7,
                "partition": "audit",
                "stratum": "c1|low",
                "inclusion_probability": 0.25,
                "seed": 42,
                "sampled_at": None,
            }
        ]
    )
    tenant = uuid4()
    rows = await gate_reads.audit_rows(FakePool(conn), tenant, "s1")
    assert rows[0]["unit_id"] == "7" and rows[0]["inclusion_probability"] == 0.25
    assert rows[0]["seed"] == 42 and rows[0]["sampled_at"] is None
    scoped = [(q, args) for q, args in conn.queries if "audit_partitions" in q]
    assert scoped and scoped[0][1] == (tenant, "s1")
    # the RLS setting was applied inside the fake transaction
    assert conn.set_configs and conn.set_configs[0] == (str(tenant),)
    assert seen[0] == str(tenant)


@pytest.mark.asyncio
async def test_codebook_categories_maps_registry_rows(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []
    install_fake_tenant_connection(monkeypatch, seen)
    conn = FakeConn(
        [
            {
                "code_id": "c1",
                "name": "Pause",
                "brief_definition": "a pause",
                "inclusion_criteria": "i",
                "exclusion_criteria": "e",
                "examples": ["quote"],
                "theory": {"parent_id": "root"},
            }
        ]
    )
    doc = await gate_reads.codebook_categories(FakePool(conn), uuid4(), uuid4())
    categories = doc["categories"]
    assert isinstance(categories, list)
    code = categories[0]
    assert code["id"] == "c1"
    assert code["examples"] == ["quote"]
    assert code["theory"]["parent_id"] == "root"


@pytest.mark.asyncio
async def test_latest_release_payload_parses_json(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []
    install_fake_tenant_connection(monkeypatch, seen)
    conn = FakeConn(['{"machine_profile": {"model_id": "qwen3:8b"}}'])
    payload = await gate_reads.latest_release_payload(FakePool(conn), uuid4(), uuid4())
    assert machine_profile_from_manifest(payload)["model_id"] == "qwen3:8b"


@pytest.mark.asyncio
async def test_run_metric_block_returns_dict_blocks(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []
    install_fake_tenant_connection(monkeypatch, seen)
    conn = FakeConn(['{"saturation": {"new_codes": 2, "base_unique": 60}}'])
    block = await gate_reads.run_metric_block(FakePool(conn), uuid4(), uuid4(), "saturation")
    assert block == {"new_codes": 2, "base_unique": 60}
