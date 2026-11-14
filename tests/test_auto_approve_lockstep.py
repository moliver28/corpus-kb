"""The autoApprove single derivation, pinned end to end (novice-UX W3c).

ONE rule: a tool is auto-approvable only when it performs no store write
and no ingest write under any declared parameter. The registry carries
the two sides: ``AUTO_APPROVE_WRITE_TOOLS`` names the write rows, and
``SERVER_AUTO_APPROVE`` lists the safe remainder sorted. These tests
rebuild the MCP tool universe from the catalogue plus the registry and
pin the emitted list to the rule, so a new op that skips classification
fails here with its name — and so every host template byte-carries the
same list instead of drifting by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

from corpus_kb.contracts import OPERATIONS
from corpus_kb.surface_registry import (
    AUTO_APPROVE_WRITE_TOOLS,
    SERVER_AUTO_APPROVE,
    SURFACES,
)

_HOST_CONFIGS = (
    ("claude", "mcp-configs/claude-code.json"),
    ("codex", "mcp-configs/codex.json"),
    ("opencode", "opencode.json"),
)


def _mcp_universe() -> set[str]:
    """Every live MCP tool name: catalogue rows plus registry rows."""
    universe = {spec.route for op in OPERATIONS for spec in op.surfaces if spec.kind == "mcp"}
    universe |= {s.mcp_tool for s in SURFACES if "mcp" in s.surfaces and s.mcp_tool}
    return universe


def test_emitted_list_equals_the_derivation() -> None:
    """The emitted list is exactly universe minus writes, sorted."""
    assert list(SERVER_AUTO_APPROVE) == sorted(_mcp_universe() - AUTO_APPROVE_WRITE_TOOLS)


def test_every_tool_is_classified_exactly_once() -> None:
    """A tool rides one set; an unclassified op fails with its name."""
    universe = _mcp_universe()
    safe = set(SERVER_AUTO_APPROVE)
    overlap = safe & AUTO_APPROVE_WRITE_TOOLS
    assert not overlap, f"tools ride both sets: {', '.join(sorted(overlap))}"
    unclassified = universe - safe - AUTO_APPROVE_WRITE_TOOLS
    assert not unclassified, f"unclassified MCP tools: {', '.join(sorted(unclassified))}"


def test_write_rows_name_live_tools() -> None:
    """A write row that names no live tool is stale and fails."""
    stale = AUTO_APPROVE_WRITE_TOOLS - _mcp_universe()
    assert not stale, f"write rows name no live MCP tool: {', '.join(sorted(stale))}"


def test_hosts_carry_the_derived_list() -> None:
    """All three autoApprove hosts carry the derived list verbatim.

    Cursor carries no key by format (the validator rejects the key for
    Cursor), and Codex ignores the key at runtime (audit P1-6) — the
    list stays because the template must match the Claude shape.
    """
    root = Path(__file__).resolve().parent.parent
    for name, rel in _HOST_CONFIGS:
        config = json.loads((root / rel).read_text(encoding="utf-8"))
        container = config.get("mcp", {}) if name == "opencode" else config.get("mcpServers", {})
        server = container["corpus-kb"]
        assert server["autoApprove"] == list(SERVER_AUTO_APPROVE), rel
