"""MCP validators for the editor formats without autoApprove.

Cursor and ZCode are the two mcpServers-family formats that carry NO
``autoApprove`` key, so their validators share one shape: name,
description, command (string), args, and env are required, and
autoApprove is absent. Split out of ``validate_configs_mcp.py`` for the
250-line rule; the bodies are byte-identical moves and
``validate_configs_mcp`` re-imports them, so every import path keeps
working (both package and script contexts).
"""

from __future__ import annotations

from typing import Any

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
    if command is not None and not isinstance(command, str):
        errors.append(
            f"{prefix}: 'command' must be a string (e.g. 'corpus-kb'), got {type(command).__name__}"
        )

    return errors


# ---------------------------------------------------------------------------
# ZCode format validator
# ---------------------------------------------------------------------------


def validate_zcode_config(config: dict[str, Any]) -> list[str]:
    """Validate a ZCode format config (mcp.servers key).

    Required structure:
        {
            "mcp": {
                "servers": {
                    "<tool-name>": {
                        "name": "...",
                        "description": "...",
                        "command": "executable",      # MUST be string
                        "args": ["arg1", ...],
                        "env": {},
                        # autoApprove is NOT used by ZCode
                    }
                }
            }
        }

    ZCode reads the nested ``mcp.servers`` envelope from its config files;
    a top-level ``mcpServers`` key is only the ``.agents/mcp.json``
    fallback, not the shape this template publishes.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config must be a JSON object"]

    mcp = config.get("mcp")
    if not isinstance(mcp, dict) or "servers" not in mcp:
        errors.append("Missing required 'mcp.servers' key for ZCode format")
        return errors

    if "mcpServers" in config:
        errors.append(
            "Found 'mcpServers' key — that is the fallback file shape; use nested 'mcp.servers'"
        )

    servers = mcp["servers"]
    if not isinstance(servers, dict) or not servers:
        errors.append("'mcp.servers' must be a non-empty object")
        return errors

    for tool_name, tool_config in servers.items():
        prefix = f"mcp.servers.{tool_name}"
        errors.extend(_validate_zcode_tool(prefix, tool_config))

    return errors


def _validate_zcode_tool(prefix: str, tool_config: dict[str, Any]) -> list[str]:
    """Validate a single server entry within ZCode mcp.servers."""
    errors: list[str] = []

    if not isinstance(tool_config, dict):
        return [f"{prefix}: tool config must be an object"]

    # ZCode requires: name, description, command, args, env
    # autoApprove is NOT required (ZCode doesn't use it)
    for field in ("name", "description", "command", "args", "env"):
        if field not in tool_config:
            errors.append(f"{prefix}: missing required field '{field}'")

    # command must be a string
    command = tool_config.get("command")
    if command is not None and not isinstance(command, str):
        errors.append(
            f"{prefix}: 'command' must be a string (e.g. 'corpus-kb'), got {type(command).__name__}"
        )

    return errors
