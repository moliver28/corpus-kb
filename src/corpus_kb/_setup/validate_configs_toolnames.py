"""Master allowlist of MCP tool names valid in ``autoApprove`` lists.

The set DERIVES from the one source of truth, not from a hand list: the
parity catalogue (``corpus_kb.contracts``) plus ``surface_registry``'s
``SURFACES``. Every row that declares the MCP kind joins; CLI-only and
HTTP-only ids stay out. The lockstep gate (``poe toolname-lockstep``,
ci.yml / toolname-lockstep, issue #416) pins the set in both directions,
so a new MCP tool cannot silently fall out of the allowlist (the r9
class: a registry row landed, its allowlist entry did not).

Also owns the shared autoApprove list rule used by the OpenCode and
Claude Code validators.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _derive_valid_tool_names() -> frozenset[str]:
    """Every live MCP tool name: catalogue rows plus registry rows.

    A name joins when its row declares the MCP kind: a catalogue
    surface spec with kind ``mcp``, or a registry row with ``"mcp"`` in
    its surface set and a tool name. Names that only an HTTP route or a
    CLI subcommand carries never join.
    """
    from corpus_kb.contracts import OPERATIONS
    from corpus_kb.contracts.model import MCP
    from corpus_kb.surface_registry import SURFACES

    names = {spec.route for op in OPERATIONS for spec in op.surfaces if spec.kind == MCP}
    names |= {s.mcp_tool for s in SURFACES if "mcp" in s.surfaces and s.mcp_tool}
    return frozenset(names)


def _derive_in_script_context() -> frozenset[str]:
    """Derive when ``corpus_kb`` is not installed (script context).

    The CI validate-configs job installs only PyYAML, and ``corpus_kb``
    stays UNIMPORTABLE there by design: the engine import chain needs
    the full dependency set. ``surface_registry`` is stdlib-only, so
    this loads it by path (the ``agent_config_autoapprove`` pattern)
    and unions its two classified sets. The engine-side lockstep test
    pins that union to the full MCP tool universe, so the two paths can
    never disagree.
    """
    registry_path = (
        Path(__file__).resolve().parents[3] / "src" / "corpus_kb" / "surface_registry.py"
    )
    spec = importlib.util.spec_from_file_location("corpus_surface_registry", registry_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load surface_registry by path: {registry_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return frozenset(module.SERVER_AUTO_APPROVE) | frozenset(module.AUTO_APPROVE_WRITE_TOOLS)
    finally:
        sys.modules.pop(spec.name, None)


try:
    VALID_TOOL_NAMES: frozenset[str] = _derive_valid_tool_names()
except ImportError:  # pragma: no cover - script context (CI validate job)
    VALID_TOOL_NAMES = _derive_in_script_context()


def _auto_approve_errors(prefix: str, auto_approve: object, with_valid_list: bool) -> list[str]:
    """Shared autoApprove rule: a non-empty list of known tool names.

    ``with_valid_list`` adds the sorted allowlist to every unknown-name
    error (the OpenCode rendering); the Claude/Codex rendering names only
    the unknown tool.
    """
    if auto_approve is None:
        return []
    if not isinstance(auto_approve, list):
        return [
            f"{prefix}: 'autoApprove' must be a list of tool names, "
            f"got {type(auto_approve).__name__}"
        ]
    if not auto_approve:
        return [f"{prefix}: 'autoApprove' must not be empty"]
    errors: list[str] = []
    for tool in auto_approve:
        if tool not in VALID_TOOL_NAMES:
            if with_valid_list:
                errors.append(
                    f"{prefix}: invalid tool name in autoApprove: '{tool}' — "
                    f"must be one of: {', '.join(sorted(VALID_TOOL_NAMES))}"
                )
            else:
                errors.append(f"{prefix}: invalid tool name in autoApprove: '{tool}'")
    return errors
