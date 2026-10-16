"""U5/P7 MECE surface-parity tests — the binding transport contract.

The release lifecycle ops (``assess``/``request``/``review``/``release``/
``status``/``compare``) and every research surface MUST be reachable on ALL
THREE transports: CLI (real registered commands), MCP (names validated in
``validate_configs.VALID_TOOL_NAMES`` + real tool functions), and HTTP
(real routes in ``create_http_app()``). A missing surface anywhere fails
this suite.

Since the full-surface-parity wave the registry is complete:
``research-cycle`` and ``research-demo`` gained their MCP + HTTP legs as
synchronous long-running calls, and ``TRANSPORT_ALLOWLIST`` is empty. The
tests below pin that end state — no exceptions left behind, and the
allowlist mechanism stays available (empty) for a future honest gap.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from corpus_kb import surface_registry as reg
from corpus_kb.surface_registry_parity import (
    TRANSPORT_ALLOWLIST,
    assert_full_surface_parity,
    assert_parity_outside_allowlist,
    surface_coverage_gaps,
)

ROOT = Path(__file__).resolve().parent.parent

RELEASE_SURFACE_NAMES = (
    "release-assess",
    "release-request",
    "release-review",
    "release-execute",
    "release-status",
    "release-compare",
)

EXPECTED_HTTP_METHODS = {"release-status": "GET"}


def _surface(name: str) -> reg.Surface:
    for surface in reg.SURFACES:
        if surface.name == name:
            return surface
    raise AssertionError(f"surface {name} not registered")


def _release_surfaces() -> list[reg.Surface]:
    return [_surface(name) for name in RELEASE_SURFACE_NAMES]


def _validate_configs() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "validate_configs_parity_test",
        ROOT / "src" / "corpus_kb" / "_setup" / "validate_configs.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# U5: the release lifecycle on all three transports (REAL wiring, not rows)
# ---------------------------------------------------------------------------


def test_release_operations_declare_all_three_transports() -> None:
    for surface in _release_surfaces():
        assert surface.cli_path, surface.name
        assert surface.mcp_tool, surface.name
        assert surface.http_route and surface.http_route.startswith("/"), surface.name


def test_release_cli_commands_are_registered() -> None:
    from corpus_kb import cli

    registered = {str(command.name) for command in cli.release_app.registered_commands}
    assert registered == {"assess", "request", "review", "release", "status", "compare"}
    for surface in _release_surfaces():
        _group, command = surface.cli_path.split()
        assert command in registered, surface.name


def test_release_mcp_tools_are_validated_and_implemented() -> None:
    from corpus_kb.tools import release_tools

    validate_configs = _validate_configs()
    for surface in _release_surfaces():
        assert surface.mcp_tool in validate_configs.VALID_TOOL_NAMES, surface.name
        assert callable(getattr(release_tools, surface.mcp_tool, None)), surface.name


def test_release_http_routes_exist_in_the_real_route_table() -> None:
    from corpus_kb.api.http import create_http_app

    route_table = {route.path: route.methods - {"HEAD"} for route in create_http_app().routes}
    for surface in _release_surfaces():
        assert surface.http_route in route_table, surface.name
        expected = EXPECTED_HTTP_METHODS.get(surface.name, "POST")
        assert route_table[surface.http_route] == {expected}, surface.name


def test_pre_existing_mechanical_routes_exist_too() -> None:
    """The P7 sweep wired the mechanically HTTP-shaped legacy surfaces; any
    registry http_route must resolve against the real route table."""
    from corpus_kb.api.http import create_http_app

    route_table = {route.path for route in create_http_app().routes}
    for surface in reg.SURFACES:
        if surface.http_route:
            assert surface.http_route in route_table, surface.name


# ---------------------------------------------------------------------------
# The documented allowlist: honest staging, never silent gaps
# ---------------------------------------------------------------------------


def test_full_parity_holds_everywhere() -> None:
    """End state: no documented gaps, no raw gaps — both contracts green."""
    assert TRANSPORT_ALLOWLIST == {}
    assert surface_coverage_gaps(reg.SURFACES, TRANSPORT_ALLOWLIST) == []
    assert_parity_outside_allowlist(reg.SURFACES)  # must not raise
    assert_full_surface_parity(reg.SURFACES)  # the raw contract now holds


def test_allowlist_stays_empty_and_referenceable() -> None:
    """The mechanism stays available; today it documents zero exceptions."""
    surface_names = {surface.name for surface in reg.SURFACES}
    assert set(TRANSPORT_ALLOWLIST) <= surface_names
    raw_gaps = {
        gap.split(": ")[0]: gap.split(": ")[1] for gap in surface_coverage_gaps(reg.SURFACES)
    }
    assert raw_gaps == {}
    assert set(raw_gaps) == set(TRANSPORT_ALLOWLIST)


def test_allowlist_is_documented_in_the_reconciliation_doc() -> None:
    text = (ROOT / "docs" / "research" / "upgrade-reconciliation.md").read_text(encoding="utf-8")
    assert "TRANSPORT_ALLOWLIST" in text
    assert "research-cycle" in text and "research-demo" in text


# ---------------------------------------------------------------------------
# D4: stable payload schemas (one handler per op, identical across surfaces)
# ---------------------------------------------------------------------------


def test_release_payload_schemas_are_stable() -> None:
    from corpus_kb.handlers import release_handlers as rh

    samples = {
        rh.AssessResult: rh.AssessResult(
            codebook_version_id="v",
            profile="team-codebook",
            enforcement="warn",
            gate_results=[{"gate_id": "g", "status": "not_evaluable"}],
            required_gate_ids=["g"],
            required_failing=[],
            required_not_evaluable=["g"],
            all_required_pass_without_waivers=False,
            data_sufficiency={"codebook": False},
        ),
        rh.RequestResult: rh.RequestResult(
            status="requested", release_id="r", manifest_sha256="x", profile="team-codebook"
        ),
        rh.ReviewPacket: rh.ReviewPacket(
            release_id="r",
            release_state="draft_candidate",
            profile="team-codebook",
            enforcement="warn",
            evidence=[],
            failing_gates=[],
            not_evaluable_gates=[],
            waived_gates=[],
            unmet_required_gates=[],
            next_actions=["corpus-kb release assess --codebook-version-id <uuid>"],
            recommended_next_action="corpus-kb release assess --codebook-version-id <uuid>",
        ),
        rh.ReleaseOutcome: rh.ReleaseOutcome(
            status="released",
            release_id="r",
            approver="dana",
            enforcement="warn",
            unmet_required_gates=[],
            warnings=[],
        ),
        rh.StatusReport: rh.StatusReport(
            codebook_version_id="v",
            current_stage=rh.STAGE_PRE_DRAFT,
            release_state="none",
            blocking_criteria=[],
            recommended_next_action="corpus-kb release request",
            active_release=None,
            draft=None,
            pending_change_proposals=[],
            data_sufficiency={},
        ),
    }
    expected = {
        rh.AssessResult: (
            "status",
            "codebook_version_id",
            "profile",
            "enforcement",
            "gate_results",
            "required_gate_ids",
            "required_failing",
            "required_not_evaluable",
            "all_required_pass_without_waivers",
            "data_sufficiency",
        ),
        rh.RequestResult: ("status", "release_id", "manifest_sha256", "profile"),
        rh.ReviewPacket: (
            "status",
            "release_id",
            "release_state",
            "profile",
            "enforcement",
            "evidence",
            "failing_gates",
            "not_evaluable_gates",
            "waived_gates",
            "unmet_required_gates",
            "next_actions",
            "recommended_next_action",
        ),
        rh.ReleaseOutcome: (
            "status",
            "release_id",
            "approver",
            "enforcement",
            "unmet_required_gates",
            "warnings",
            "reason",
        ),
        rh.StatusReport: (
            "status",
            "codebook_version_id",
            "current_stage",
            "release_state",
            "blocking_criteria",
            "recommended_next_action",
            "active_release",
            "draft",
            "pending_change_proposals",
            "data_sufficiency",
        ),
    }
    for payload_type, keys in expected.items():
        sample = samples[payload_type]
        assert tuple(sample.to_payload()) == keys, payload_type.__name__


def test_compare_payload_carries_the_diff_fields() -> None:
    from corpus_kb.handlers.release_handlers import CompareResult

    payload = CompareResult(
        old_release_id="a", new_release_id="b", diff={"identical": True}
    ).to_payload()
    assert set(payload) == {
        "status",
        "old_release_id",
        "new_release_id",
        "identical",
    }


# ---------------------------------------------------------------------------
# D5: the doctor surfaces the parity contract
# ---------------------------------------------------------------------------


def test_doctor_surface_parity_check_green() -> None:
    from corpus_kb._setup import doctor_research as dr

    check = dr.surface_parity_check()
    assert check.status == dr.STATUS_OK
    assert "release-assess" in check.detail
    assert check.doc == dr.DOC_RECONCILIATION


def test_doctor_surface_parity_check_red_on_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb import surface_registry as reg
    from corpus_kb._setup import doctor_research as dr

    stripped = tuple(
        reg.Surface(
            name=surface.name,
            cli_path=surface.cli_path,
            mcp_tool=surface.mcp_tool,
            description=surface.description,
            http_route=None,
        )
        if surface.name == "release-status"
        else surface
        for surface in reg.SURFACES
    )
    monkeypatch.setattr(reg, "SURFACES", stripped)
    check = dr.surface_parity_check()
    assert check.status == dr.STATUS_FAIL
    assert "release-status" in check.detail
