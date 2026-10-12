"""MECE consistency checks for surface_registry.SURFACES.

Internal-consistency contract only TODAY: every surface declares at least
one transport, CLI paths resolve to real registered commands, MCP tool names
are known to validate-configs, wrapper covers reference real surfaces, and
names are unique. FULL transport parity (cli+mcp+http for every operation)
is NOT enforced yet — research surfaces have ``http_route=None`` by design
and the enforcement flip lands in P5 (see
``corpus_kb.surface_registry_parity.assert_full_surface_parity``).
"""

from __future__ import annotations

import re

import pytest

from corpus_kb import surface_registry as reg
from corpus_kb.surface_registry_parity import (
    SurfaceParityError,
    assert_full_surface_parity,
    surface_coverage_gaps,
)

KNOWN_TRANSPORTS = {"cli", "mcp", "http"}


def _transports(surface: reg.Surface) -> set[str]:
    declared: set[str] = set()
    if surface.cli_path:
        declared.add("cli")
    if surface.mcp_tool:
        declared.add("mcp")
    if surface.http_route:
        declared.add("http")
    return declared


def test_every_surface_declares_at_least_one_known_transport() -> None:
    assert reg.SURFACES, "surface registry lost all surfaces"
    for surface in reg.SURFACES:
        declared = _transports(surface)
        assert declared, f"{surface.name} declares no transport"
        assert declared <= KNOWN_TRANSPORTS, (
            f"{surface.name} declares unknown transports {declared - KNOWN_TRANSPORTS}"
        )


def test_surface_names_unique() -> None:
    names = [s.name for s in reg.SURFACES]
    assert len(names) == len(set(names)), "duplicate surface names"


def test_cli_paths_resolve_to_registered_commands() -> None:
    from corpus_kb import cli

    sub_apps = {
        "research": cli.research_app,
        "coding": cli.coding_app,
        "codebook": cli.codebook_app,
        "review": cli.review_app,
    }

    def _commands_of(typer_app: object) -> set[str]:
        registered = getattr(typer_app, "registered_commands", [])
        return {str(c.name) for c in registered}  # typer models expose .name

    for surface in reg.SURFACES:
        parts = surface.cli_path.split()
        assert len(parts) in (1, 2), f"{surface.name}: cli_path must be '<app> <cmd>'"
        if len(parts) == 1:
            # Top-level command (e.g. "setup") — resolved via the main app.
            top = _commands_of(cli.app)
            assert parts[0] in top, f"{surface.name}: top-level command '{parts[0]}' unregistered"
            continue
        group, command = parts
        assert group in sub_apps, f"{surface.name}: unknown CLI group '{group}'"
        registered = _commands_of(sub_apps[group])
        assert command in registered, (
            f"{surface.name}: '{group} {command}' is not a registered CLI command"
        )


def test_mcp_tools_are_validated_names() -> None:
    from corpus_kb._setup import validate_configs

    for surface in reg.SURFACES:
        if not surface.mcp_tool:
            continue  # CLI-only surfaces render "(none)" — allowed by design
        assert re.fullmatch(r"[a-z][a-z0-9_]*", surface.mcp_tool), (
            f"{surface.name}: mcp_tool '{surface.mcp_tool}' not snake_case"
        )
        assert surface.mcp_tool in validate_configs.VALID_TOOL_NAMES, (
            f"{surface.name}: mcp_tool '{surface.mcp_tool}' missing from "
            "validate_configs.VALID_TOOL_NAMES"
        )


def test_http_routes_when_present_are_absolute_paths() -> None:
    for surface in reg.SURFACES:
        if surface.http_route is not None:
            assert surface.http_route.startswith("/"), (
                f"{surface.name}: http_route must be an absolute path"
            )


def test_wrapper_covers_reference_real_surfaces() -> None:
    surface_names = {s.name for s in reg.SURFACES}
    for wrapper in reg.WRAPPERS:
        assert wrapper.covers, f"wrapper {wrapper.name} covers nothing"
        unknown = set(wrapper.covers) - surface_names
        assert not unknown, f"wrapper {wrapper.name} covers unknown surfaces {sorted(unknown)}"


def test_wrapper_covers_are_mece() -> None:
    """No surface may be fronted by two active wrappers (double coverage)."""
    seen: dict[str, str] = {}
    for wrapper in reg.ACTIVE_WRAPPERS:
        for surface_name in wrapper.covers:
            assert surface_name not in seen, (
                f"surface {surface_name} covered by both {seen[surface_name]} and {wrapper.name}"
            )
            seen[surface_name] = wrapper.name


def test_payload_schemas_contract_placeholder() -> None:
    """The Surface NamedTuple carries no payload schema field today.

    When a schema field is added (P5), this test must be extended to assert
    schema presence for every surface; keeping the check explicit prevents
    the schema requirement from being silently skipped.
    """
    surface_fields = set(reg.Surface._fields)
    assert "payload_schema" not in surface_fields, (
        "Surface gained a payload_schema field: extend this test to require one for every surface"
    )


# ---------------------------------------------------------------------------
# Parity helper behavior (synthetic registries — enforcement against the
# live registry is deliberately deferred to P5)
# ---------------------------------------------------------------------------


def _synthetic(name: str, cli: str, mcp: str, http: str | None) -> object:
    return type("_S", (), {"name": name, "cli_path": cli, "mcp_tool": mcp, "http_route": http})()


def test_parity_helper_passes_when_all_transports_covered() -> None:
    registry = [
        _synthetic("op", "research op", "research_op", "/api/research/op"),
        _synthetic("op2", "research op2", "research_op2", "/api/research/op2"),
    ]
    assert surface_coverage_gaps(registry) == []
    assert_full_surface_parity(registry)  # must not raise


def test_parity_helper_lists_every_gap_not_just_the_first() -> None:
    registry = [
        _synthetic("a", "research a", "research_a", None),
        _synthetic("b", "research b", "", None),
    ]
    gaps = surface_coverage_gaps(registry)
    assert gaps == ["a: missing http", "b: missing mcp, http"]
    with pytest.raises(SurfaceParityError) as excinfo:
        assert_full_surface_parity(registry)
    message = str(excinfo.value)
    assert "a: missing http" in message
    assert "b: missing mcp, http" in message
    assert "2 of 2" in message


def test_live_registry_currently_lacks_http_parity_by_design() -> None:
    """Documents today's honest state: no research surface has an HTTP route.

    This pins the PRE-enforcement state so the P5 flip is a deliberate,
    reviewed change (this test is expected to be REPLACED by enforcement).
    """
    gaps = surface_coverage_gaps(reg.SURFACES)
    assert gaps, "P5 landed: all surfaces have transports — flip enforcement on"
    assert all("http" in gap for gap in gaps)
    with pytest.raises(SurfaceParityError):
        assert_full_surface_parity(reg.SURFACES)
