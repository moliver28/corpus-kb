"""Wave-2 cycle gate wiring (offline): U19 agreement routing into the
gray-zone halt queue (adversarial review F2) and the unreleased-draft
halt gate (F8). No Postgres, no Ollama — the DB reads are faked; the
evidence math and gate decisions are tested pure.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from corpus_kb.config import get_default_config
from corpus_kb.research.cycle_gates import (
    DEFAULT_HALT_ON,
    GATE_GRAY_ZONE_ESCALATION,
    GATE_UNRELEASED_DRAFT,
)
from corpus_kb.research.release.gate_reads import agreement_routing_evidence

TENANT = UUID("00000000-0000-0000-0000-000000000001")
RUN_1 = "00000000-0000-0000-0000-0000000000a1"
RUN_2 = "00000000-0000-0000-0000-0000000000a2"

TWO_RUN_RECEIPT = {"run_id": RUN_1, "run_ids": [RUN_1, RUN_2]}
ONE_RUN_RECEIPT = {"run_id": RUN_1}


# ---------------------------------------------------------------------------
# Pure evidence: per-unit cross-run agreement from label blocks
# ---------------------------------------------------------------------------


def test_agreement_routing_evidence_scores_and_flags_routed() -> None:
    run1 = {"u1": "cost", "u2": "cost", "u3": "cost"}
    run2 = {"u1": "cost", "u2": "price", "u3": "cost"}
    evidence = agreement_routing_evidence([run1, run2], ["u3"])
    units = {str(u["unit_id"]): u for u in evidence["units"] if isinstance(u, dict)}
    assert units["u1"]["agreement"] == 1.0  # same label in both runs
    assert units["u2"]["agreement"] == 0.0  # the runs disagree
    assert units["u3"]["agreement"] == 1.0
    assert units["u3"]["routed"] is True  # already in the gray-zone queue
    assert units["u1"]["routed"] is False


def test_agreement_routing_evidence_without_multi_run_data_is_not_measured() -> None:
    evidence = agreement_routing_evidence([{"u1": "cost"}], [])
    units = evidence["units"]
    assert isinstance(units, list) and len(units) == 1
    row = units[0]
    assert isinstance(row, dict)
    assert row["agreement"] is None  # honest: one run measures nothing
    assert agreement_routing_evidence([], []) == {"units": []}


# ---------------------------------------------------------------------------
# The cycle-side U19 wire-in: gray-zone halt from low agreement
# ---------------------------------------------------------------------------


def test_cycle_gray_zone_from_agreement_raises_existing_halt_gate() -> None:
    from corpus_kb.research.cycle_gates import gate_gray_zone_from_agreement

    low = [
        {"unit_id": "u1", "agreement": 0.4},
        {"unit_id": "u2", "agreement": 0.6},
    ]
    finding = gate_gray_zone_from_agreement(low, ["u2"], threshold=0.8)
    assert finding is not None
    assert finding.gate == GATE_GRAY_ZONE_ESCALATION
    assert finding.detail["unit_ids"] == ["u1"]
    assert finding.detail["threshold"] == 0.8


def test_cycle_gray_zone_from_agreement_quiet_when_all_routed_or_unenforced() -> None:
    from corpus_kb.research.cycle_gates import gate_gray_zone_from_agreement

    low = [{"unit_id": "u1", "agreement": 0.4}]
    assert gate_gray_zone_from_agreement(low, ["u1"], threshold=0.8) is None
    assert gate_gray_zone_from_agreement(low, [], threshold=None) is None
    assert gate_gray_zone_from_agreement([], [], threshold=0.8) is None


@pytest.fixture()
def _quiet_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence the pending-proposal read so only the agreement wire moves."""
    from corpus_kb.research import cycle_reads

    async def zero(_pool: Any, _tenant: UUID, _run_id: UUID) -> int:
        return 0

    monkeypatch.setattr(cycle_reads, "pending_proposals", zero)


def _agreement_cfg() -> dict[str, Any]:
    return dict(get_default_config())  # team-codebook: routing threshold 0.80


async def test_low_agreement_units_fire_the_gray_zone_finding(
    monkeypatch: pytest.MonkeyPatch, _quiet_reads: None
) -> None:
    from corpus_kb.research import cycle_reads

    async def fake_blocks(_pool: Any, _tenant: UUID, run_ids: list[str]) -> list[dict[str, str]]:
        assert len(run_ids) == 2
        return [{"u1": "cost", "u2": "cost"}, {"u1": "cost", "u2": "price"}]

    async def no_routed(_pool: Any, _tenant: UUID) -> list[str]:
        return []

    monkeypatch.setattr(cycle_reads, "run_label_blocks", fake_blocks)
    monkeypatch.setattr(cycle_reads, "routed_unit_ids", no_routed)

    findings = await cycle_reads.gate_findings_for(
        "inductive", TWO_RUN_RECEIPT, None, TENANT, _agreement_cfg()
    )
    agreement = [f for f in findings if f.detail.get("threshold") is not None]
    assert len(agreement) == 1
    assert agreement[0].gate == GATE_GRAY_ZONE_ESCALATION
    assert agreement[0].detail["unit_ids"] == ["u2"]  # u1 agreed; u2 did not
    assert agreement[0].detail["threshold"] == 0.80
    # The gate is halt-enabled by default.
    assert agreement[0].gate in DEFAULT_HALT_ON


async def test_routed_low_agreement_units_do_not_refire(
    monkeypatch: pytest.MonkeyPatch, _quiet_reads: None
) -> None:
    from corpus_kb.research import cycle_reads

    async def fake_blocks(_pool: Any, _tenant: UUID, _run_ids: list[str]) -> list[dict[str, str]]:
        return [{"u1": "cost"}, {"u1": "price"}]  # u1 disagreed...

    async def already_routed(_pool: Any, _tenant: UUID) -> list[str]:
        return ["u1"]  # ...but sits in the review queue already

    monkeypatch.setattr(cycle_reads, "run_label_blocks", fake_blocks)
    monkeypatch.setattr(cycle_reads, "routed_unit_ids", already_routed)

    findings = await cycle_reads.gate_findings_for(
        "inductive", TWO_RUN_RECEIPT, None, TENANT, _agreement_cfg()
    )
    assert all(f.detail.get("threshold") is None for f in findings)


async def test_single_run_and_unconfigured_profile_are_honestly_quiet(
    monkeypatch: pytest.MonkeyPatch, _quiet_reads: None
) -> None:
    from corpus_kb.research import cycle_reads

    async def fail_blocks(_pool: Any, _tenant: UUID, _run_ids: list[str]) -> list[dict[str, str]]:
        raise AssertionError("label blocks must not load without multi-run data")

    monkeypatch.setattr(cycle_reads, "run_label_blocks", fail_blocks)

    # One run: no cross-run agreement exists — nothing to evaluate.
    single = await cycle_reads.gate_findings_for(
        "inductive", ONE_RUN_RECEIPT, None, TENANT, _agreement_cfg()
    )
    assert single == []
    # No config: the threshold is unknown — never a routing signal.
    none_cfg = await cycle_reads.gate_findings_for("inductive", TWO_RUN_RECEIPT, None, TENANT, None)
    assert none_cfg == []
    # A profile without a routing threshold (exploratory) does not fire.
    cfg = _agreement_cfg()
    research = dict(cfg["research"])
    release = dict(research["release"])
    release["default_profile"] = "exploratory"
    research["release"] = release
    cfg["research"] = research
    quiet = await cycle_reads.gate_findings_for("inductive", TWO_RUN_RECEIPT, None, TENANT, cfg)
    assert quiet == []


# ---------------------------------------------------------------------------
# F8: a blocked_unreleased_draft deductive stage IS a gate finding
# ---------------------------------------------------------------------------


def test_blocked_deductive_receipt_maps_to_the_unreleased_draft_gate() -> None:
    from corpus_kb.research.cycle_gates import GATE_EXIT_CODES
    from corpus_kb.research.cycle_reads import gate_findings_for

    blocked = {
        "status": "blocked_unreleased_draft",
        "enforcement": "enforce",
        "codebook_version_id": "v-1",
        "reason": "enforcement=enforce blocks deductive runs on unreleased codebook versions",
        "fix": "release the codebook version, or re-run with --allow-draft",
    }
    findings = asyncio.run(gate_findings_for("deductive", blocked, None, TENANT))
    assert [f.gate for f in findings] == [GATE_UNRELEASED_DRAFT]
    assert "unreleased" in findings[0].reason
    assert findings[0].detail["codebook_version_id"] == "v-1"
    # The halt machinery picks it up: in the default halt set, own exit code.
    assert GATE_UNRELEASED_DRAFT in DEFAULT_HALT_ON
    assert GATE_EXIT_CODES[GATE_UNRELEASED_DRAFT] == 17


def test_unreleased_draft_gate_has_taught_copy() -> None:
    from corpus_kb.research.cycle_gates import ALL_GATES
    from corpus_kb.research.cycle_render import GATE_GUIDE

    assert GATE_UNRELEASED_DRAFT in ALL_GATES
    taught, look, action = GATE_GUIDE[GATE_UNRELEASED_DRAFT]
    assert taught and look and action
    assert "--allow-draft" in action


def test_deductive_guard_split_keeps_public_names_importable() -> None:
    """F5b: the SQL/reader split must not move the public surface."""
    from corpus_kb.research import deduction_guard
    from corpus_kb.research.release import enforcement_settings
    from corpus_kb.research.release_read import ActiveRelease as _ReadModel

    # The reader lives in the neutral module; the guard re-exports it.
    assert deduction_guard.release_guard_settings is enforcement_settings.release_guard_settings
    assert deduction_guard.ReleaseGuardSettings is enforcement_settings.ReleaseGuardSettings
    for name in (
        "ActiveRelease",
        "active_release_for_conn",
        "active_release_from_row",
        "resolve_active_release",
        "ACTIVE_RELEASE_SQL",
        "RELEASED_STATE",
    ):
        assert hasattr(deduction_guard, name), name
    assert _ReadModel is not None
