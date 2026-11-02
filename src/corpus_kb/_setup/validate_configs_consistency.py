"""Cross-config consistency checks for the editor MCP configs.

All configs (OpenCode, Claude Code, Cursor, Codex) must define the same
tool-name set, and a tool's description must be identical in every
config that defines it.
"""

from __future__ import annotations

from typing import Any


def _extract_tool_set(config: dict[str, Any], key: str) -> dict[str, str]:
    """{tool_name: description} from one editor config under ``key``."""
    return {
        name: cfg.get("description", "")
        for name, cfg in config[key].items()
        if isinstance(cfg, dict)
    }


def _tool_set_mismatches(tool_sets: dict[str, dict[str, str]]) -> list[str]:
    """Errors for configs that define a different tool-name set than the first."""
    errors: list[str] = []
    all_tool_names = [set(ts.keys()) for ts in tool_sets.values()]
    reference = all_tool_names[0]
    for fmt, names in zip(tool_sets.keys(), all_tool_names[1:], strict=False):
        if names != reference:
            missing = reference - names
            extra = names - reference
            parts = []
            if missing:
                parts.append(f"missing: {', '.join(sorted(missing))}")
            if extra:
                parts.append(f"extra: {', '.join(sorted(extra))}")
            errors.append(
                f"Cross-config tool mismatch: all configs must define the same set of tools. "
                f"Compared to first config, '{fmt}' has {'; '.join(parts)}"
            )
    return errors


def _description_mismatches(tool_sets: dict[str, dict[str, str]]) -> list[str]:
    """Errors for tools whose description differs across configs."""
    errors: list[str] = []
    all_tool_names = [set(ts.keys()) for ts in tool_sets.values()]
    common_tools = set.intersection(*all_tool_names) if all_tool_names else set()
    formats = list(tool_sets.keys())
    for tool in sorted(common_tools):
        descriptions = {fmt: tool_sets[fmt][tool] for fmt in formats}
        unique_descs = set(descriptions.values())
        if len(unique_descs) > 1:
            desc_details = "; ".join(f"{fmt}: '{desc}'" for fmt, desc in descriptions.items())
            errors.append(f"Description mismatch for tool '{tool}': {desc_details}")
    return errors


def check_cross_config_consistency(
    opencode_config: dict[str, Any] | None,
    claude_config: dict[str, Any] | None,
    cursor_config: dict[str, Any] | None,
    codex_config: dict[str, Any] | None = None,
) -> list[str]:
    """Check that all configs define the same tools with matching descriptions.

    Returns a list of error strings (empty = consistent).
    """
    errors: list[str] = []

    # Extract tool sets from each config: format -> {tool_name: description}
    sources = (
        ("opencode", opencode_config, "mcp"),
        ("claude", claude_config, "mcpServers"),
        ("cursor", cursor_config, "mcpServers"),
        ("codex", codex_config, "mcpServers"),
    )
    tool_sets = {
        name: _extract_tool_set(cfg, key) for name, cfg, key in sources if cfg and key in cfg
    }

    if len(tool_sets) < 2:
        return errors  # Nothing to cross-check

    return _tool_set_mismatches(tool_sets) + _description_mismatches(tool_sets)
