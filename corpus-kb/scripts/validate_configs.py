"""Config validation script for OpenCode CI enforcement.

Validates MCP configuration files across three editor formats:
  - OpenCode native (mcp key, command as array)
  - Claude Code (mcpServers key, command as string + args)
  - Cursor (mcpServers key, command as string + args, no autoApprove)

Usage:
    python scripts/validate_configs.py

Exit code 0 = all valid, 1 = validation errors found.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Valid tool names extracted from the corpus-kb MCP server.
# These are the only values allowed in autoApprove lists.
VALID_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "search",
        "search_context",
        "search_similar",
        "retrieve_context",
        "list_documents",
        "get_stats",
        "list_versions",
        "list_branches",
        "get_entity_relations",
        "search_graph",
        "sql_query",
        "sql_tables",
        "get_document_tags",
        "get_metadata",
        "query_document_stats",
        "sync_database",
    }
)

# ---------------------------------------------------------------------------
# OpenCode native format validator
# ---------------------------------------------------------------------------


def validate_opencode_config(config: dict[str, Any]) -> list[str]:
    """Validate an OpenCode native format config (mcp key).

    Required structure:
        {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "<tool-name>": {
                    "type": "local",
                    "description": "...",
                    "command": ["executable", "arg1", ...],   # MUST be array
                    "environment": {},
                    "autoApprove": ["tool1", "tool2", ...],   # MUST be non-empty list
                }
            }
        }

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config must be a JSON object"]

    # $schema
    if "$schema" not in config:
        errors.append("Missing required '$schema' field")

    # mcp key (not mcpServers)
    if "mcp" not in config:
        errors.append(
            "Missing required 'mcp' key — OpenCode format uses 'mcp', not 'mcpServers'"
        )
        return errors

    if "mcpServers" in config:
        errors.append(
            "Found 'mcpServers' key — this is the old format; use 'mcp' for OpenCode"
        )

    mcp = config["mcp"]
    if not isinstance(mcp, dict) or not mcp:
        errors.append("'mcp' must be a non-empty object mapping tool names to config")
        return errors

    for tool_name, tool_config in mcp.items():
        prefix = f"mcp.{tool_name}"
        errors.extend(_validate_opencode_tool(prefix, tool_config))

    return errors


def _validate_opencode_tool(prefix: str, tool_config: dict[str, Any]) -> list[str]:
    """Validate a single tool entry within the OpenCode mcp block."""
    errors: list[str] = []

    if not isinstance(tool_config, dict):
        return [f"{prefix}: tool config must be an object"]

    # Required fields
    for field in ("type", "description", "command", "environment", "autoApprove"):
        if field not in tool_config:
            errors.append(f"{prefix}: missing required field '{field}'")

    # command must be an array
    command = tool_config.get("command")
    if command is not None:
        if not isinstance(command, list):
            errors.append(
                f"{prefix}: 'command' must be an array of strings (e.g. ['corpus-kb', '--transport', 'stdio']), "
                f"got {type(command).__name__}"
            )
        elif not command:
            errors.append(f"{prefix}: 'command' array must not be empty")

    # autoApprove must be a non-empty list of valid tool names
    auto_approve = tool_config.get("autoApprove")
    if auto_approve is not None:
        if not isinstance(auto_approve, list):
            errors.append(
                f"{prefix}: 'autoApprove' must be a list of tool names, "
                f"got {type(auto_approve).__name__}"
            )
        elif len(auto_approve) == 0:
            errors.append(f"{prefix}: 'autoApprove' must not be empty")
        else:
            for tool in auto_approve:
                if tool not in VALID_TOOL_NAMES:
                    errors.append(
                        f"{prefix}: invalid tool name in autoApprove: '{tool}' — "
                        f"must be one of: {', '.join(sorted(VALID_TOOL_NAMES))}"
                    )

    return errors


# ---------------------------------------------------------------------------
# Claude Code format validator
# ---------------------------------------------------------------------------


def validate_claude_config(config: dict[str, Any]) -> list[str]:
    """Validate a Claude Code format config (mcpServers key).

    Required structure:
        {
            "mcpServers": {
                "<tool-name>": {
                    "name": "...",
                    "description": "...",
                    "command": "executable",          # MUST be string
                    "args": ["arg1", ...],
                    "env": {},
                    "autoApprove": ["tool1", ...],    # REQUIRED for Claude
                }
            }
        }

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config must be a JSON object"]

    if "mcpServers" not in config:
        errors.append("Missing required 'mcpServers' key for Claude Code format")
        return errors

    servers = config["mcpServers"]
    if not isinstance(servers, dict) or not servers:
        errors.append("'mcpServers' must be a non-empty object")
        return errors

    for tool_name, tool_config in servers.items():
        prefix = f"mcpServers.{tool_name}"
        errors.extend(_validate_claude_tool(prefix, tool_config))

    return errors


def _validate_claude_tool(prefix: str, tool_config: dict[str, Any]) -> list[str]:
    """Validate a single tool entry within Claude Code mcpServers."""
    errors: list[str] = []

    if not isinstance(tool_config, dict):
        return [f"{prefix}: tool config must be an object"]

    for field in ("name", "description", "command", "args", "autoApprove"):
        if field not in tool_config:
            errors.append(f"{prefix}: missing required field '{field}'")

    # command must be a string
    command = tool_config.get("command")
    if command is not None:
        if not isinstance(command, str):
            errors.append(
                f"{prefix}: 'command' must be a string (e.g. 'corpus-kb'), "
                f"got {type(command).__name__}"
            )

    # autoApprove validation (same as OpenCode)
    auto_approve = tool_config.get("autoApprove")
    if auto_approve is not None:
        if not isinstance(auto_approve, list):
            errors.append(
                f"{prefix}: 'autoApprove' must be a list of tool names, "
                f"got {type(auto_approve).__name__}"
            )
        elif len(auto_approve) == 0:
            errors.append(f"{prefix}: 'autoApprove' must not be empty")
        else:
            for tool in auto_approve:
                if tool not in VALID_TOOL_NAMES:
                    errors.append(
                        f"{prefix}: invalid tool name in autoApprove: '{tool}'"
                    )

    return errors


# ---------------------------------------------------------------------------
# Cursor format validator
# ---------------------------------------------------------------------------


def validate_cursor_config(config: dict[str, Any]) -> list[str]:
    """Validate a Cursor format config (mcpServers key, no autoApprove).

    Required structure:
        {
            "mcpServers": {
                "<tool-name>": {
                    "name": "...",
                    "description": "...",
                    "command": "executable",          # MUST be string
                    "args": ["arg1", ...],
                    "env": {},
                    # autoApprove is NOT used by Cursor
                }
            }
        }

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config must be a JSON object"]

    if "mcpServers" not in config:
        errors.append("Missing required 'mcpServers' key for Cursor format")
        return errors

    servers = config["mcpServers"]
    if not isinstance(servers, dict) or not servers:
        errors.append("'mcpServers' must be a non-empty object")
        return errors

    for tool_name, tool_config in servers.items():
        prefix = f"mcpServers.{tool_name}"
        errors.extend(_validate_cursor_tool(prefix, tool_config))

    return errors


def _validate_cursor_tool(prefix: str, tool_config: dict[str, Any]) -> list[str]:
    """Validate a single tool entry within Cursor mcpServers."""
    errors: list[str] = []

    if not isinstance(tool_config, dict):
        return [f"{prefix}: tool config must be an object"]

    # Cursor requires: name, description, command, args, env
    # autoApprove is NOT required (Cursor doesn't use it)
    for field in ("name", "description", "command", "args", "env"):
        if field not in tool_config:
            errors.append(f"{prefix}: missing required field '{field}'")

    # command must be a string
    command = tool_config.get("command")
    if command is not None:
        if not isinstance(command, str):
            errors.append(
                f"{prefix}: 'command' must be a string (e.g. 'corpus-kb'), "
                f"got {type(command).__name__}"
            )

    return errors


# ---------------------------------------------------------------------------
# Cross-config consistency checks
# ---------------------------------------------------------------------------


def check_cross_config_consistency(
    opencode_config: dict[str, Any] | None,
    claude_config: dict[str, Any] | None,
    cursor_config: dict[str, Any] | None,
) -> list[str]:
    """Check that all configs define the same tools with matching descriptions.

    Returns a list of error strings (empty = consistent).
    """
    errors: list[str] = []

    # Extract tool sets from each config
    tool_sets: dict[str, dict[str, str]] = {}  # format -> {tool_name: description}

    if opencode_config and "mcp" in opencode_config:
        tool_sets["opencode"] = {
            name: cfg.get("description", "")
            for name, cfg in opencode_config["mcp"].items()
            if isinstance(cfg, dict)
        }

    if claude_config and "mcpServers" in claude_config:
        tool_sets["claude"] = {
            name: cfg.get("description", "")
            for name, cfg in claude_config["mcpServers"].items()
            if isinstance(cfg, dict)
        }

    if cursor_config and "mcpServers" in cursor_config:
        tool_sets["cursor"] = {
            name: cfg.get("description", "")
            for name, cfg in cursor_config["mcpServers"].items()
            if isinstance(cfg, dict)
        }

    if len(tool_sets) < 2:
        return errors  # Nothing to cross-check

    # Check all configs define the same set of tool names
    all_tool_names = [set(ts.keys()) for ts in tool_sets.values()]
    reference = all_tool_names[0]
    for fmt, names in zip(tool_sets.keys(), all_tool_names[1:]):
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

    # Check descriptions match across configs
    common_tools = set.intersection(*all_tool_names) if all_tool_names else set()
    formats = list(tool_sets.keys())
    for tool in sorted(common_tools):
        descriptions = {fmt: tool_sets[fmt][tool] for fmt in formats}
        unique_descs = set(descriptions.values())
        if len(unique_descs) > 1:
            desc_details = "; ".join(
                f"{fmt}: '{desc}'" for fmt, desc in descriptions.items()
            )
            errors.append(f"Description mismatch for tool '{tool}': {desc_details}")

    return errors


# ---------------------------------------------------------------------------
# File-level validation (loads from disk)
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict:
    """Load and parse a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_all(project_root: Path | None = None) -> list[str]:
    """Run all validations against the project's config files.

    Returns a combined list of error strings.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    errors: list[str] = []

    # 1. Validate root opencode.json (OpenCode native format)
    opencode_path = project_root / "opencode.json"
    if opencode_path.exists():
        config = load_json(opencode_path)
        errs = validate_opencode_config(config)
        errors.extend(f"opencode.json: {e}" for e in errs)
    else:
        errors.append("opencode.json not found")

    # 2. Validate mcp-configs/*.json
    mcp_configs_dir = project_root / "mcp-configs"
    claude_config: dict | None = None
    cursor_config: dict | None = None

    if mcp_configs_dir.exists():
        for json_file in sorted(mcp_configs_dir.glob("*.json")):
            config = load_json(json_file)
            filename = json_file.name

            if filename == "opencode.json":
                # Should match root format
                errs = validate_opencode_config(config)
                errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
            elif filename == "claude-code.json":
                claude_config = config
                errs = validate_claude_config(config)
                errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
            elif filename == "cursor.json":
                cursor_config = config
                errs = validate_cursor_config(config)
                errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
            else:
                errors.append(
                    f"mcp-configs/{filename}: unknown config format, skipping"
                )
    else:
        errors.append("mcp-configs/ directory not found")

    # 3. Cross-config consistency
    if opencode_path.exists():
        opencode_config = load_json(opencode_path)
    else:
        opencode_config = None

    cross_errors = check_cross_config_consistency(
        opencode_config, claude_config, cursor_config
    )
    errors.extend(f"Cross-config: {e}" for e in cross_errors)

    # 4. Check for old format backup (informational)
    bak_path = project_root / "opencode.json.bak"
    if bak_path.exists():
        bak_config = load_json(bak_path)
        bak_errors = validate_opencode_config(bak_config)
        if not bak_errors:
            errors.append(
                "opencode.json.bak: unexpectedly passes OpenCode validation (should be old format)"
            )

    return errors


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run validation and exit with appropriate code."""
    project_root = Path(__file__).resolve().parent.parent
    errors = validate_all(project_root)

    if errors:
        print(f"CONFIG VALIDATION FAILED ({len(errors)} error(s)):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print("All config validations passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
