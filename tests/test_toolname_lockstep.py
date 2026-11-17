"""The MCP tool-name allowlist lockstep gate (issue #416).

``VALID_TOOL_NAMES`` is not a hand list. It derives from the parity
catalogue plus ``surface_registry.SURFACES``: every row that declares
the MCP kind joins; CLI-only and HTTP-only ids stay out. These tests
pin the set in both directions. An allowlist entry that names no live
MCP tool fails. A live MCP tool missing from the allowlist fails with
its name. This is the gate for the r9 drift class: a registry row
landed, its allowlist entry did not.
"""

from __future__ import annotations

from corpus_kb._setup.validate_configs_toolnames import (
    VALID_TOOL_NAMES,
    _derive_in_script_context,
    _derive_valid_tool_names,
)
from corpus_kb.contracts import OPERATIONS
from corpus_kb.contracts.model import MCP
from corpus_kb.surface_registry import (
    AUTO_APPROVE_WRITE_TOOLS,
    SERVER_AUTO_APPROVE,
    SURFACES,
)


def _mcp_universe() -> set[str]:
    """Every live MCP tool name: catalogue rows plus registry rows."""
    universe = {spec.route for op in OPERATIONS for spec in op.surfaces if spec.kind == MCP}
    universe |= {s.mcp_tool for s in SURFACES if "mcp" in s.surfaces and s.mcp_tool}
    return universe


def test_allowlist_has_no_extra_names() -> None:
    """An allowlist entry must name a live MCP tool."""
    extra = VALID_TOOL_NAMES - _mcp_universe()
    assert not extra, f"allowlist entries name no live tool: {', '.join(sorted(extra))}"


def test_allowlist_has_no_missing_names() -> None:
    """Every live MCP tool must sit in the allowlist."""
    missing = _mcp_universe() - VALID_TOOL_NAMES
    assert not missing, f"live MCP tools missing from the allowlist: {', '.join(sorted(missing))}"


def test_module_set_is_the_derivation() -> None:
    """The imported set equals a fresh derivation (no second source)."""
    assert _derive_valid_tool_names() == VALID_TOOL_NAMES


def test_script_context_derivation_agrees() -> None:
    """The PyYAML-only path names the same tools.

    The validate-configs CI job cannot import ``corpus_kb``; its
    registry-by-path union must equal the engine derivation, or the
    two contexts would validate different allowlists.
    """
    assert _derive_in_script_context() == VALID_TOOL_NAMES


def test_excluded_catalogue_tools_stay_out() -> None:
    """A catalogue op the server excludes must not ride the allowlist.

    ``EXCLUDED_TOOLS`` is empty by design. If an exclusion ever lands,
    the derivation must learn it; until then this test stays red.
    """
    from corpus_kb.mcp_tools import EXCLUDED_TOOLS

    overlap = VALID_TOOL_NAMES & set(EXCLUDED_TOOLS)
    assert not overlap, f"excluded ops ride the allowlist: {', '.join(sorted(overlap))}"


def test_universe_matches_the_auto_approve_classification() -> None:
    """The allowlist universe is the one the auto-approve lockstep splits."""
    classified = set(SERVER_AUTO_APPROVE) | set(AUTO_APPROVE_WRITE_TOOLS)
    assert _mcp_universe() == classified


def test_rows_without_the_mcp_kind_stay_out() -> None:
    """A registry row that declares no MCP kind names no allowlist entry."""
    for surface in SURFACES:
        if "mcp" not in surface.surfaces:
            assert surface.mcp_tool not in VALID_TOOL_NAMES, surface.name
