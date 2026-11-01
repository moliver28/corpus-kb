"""Per-editor MCP client config validators.

Validates one parsed config object per editor format and returns a list
of error strings (empty = valid):
  - OpenCode native (mcp key, command as array)
  - Claude Code (mcpServers key, command as string + args)
  - Cursor (mcpServers key, command as string + args, no autoApprove)

Codex configs reuse the Claude Code validator (same mcpServers +
autoApprove shape — the copy-template for Codex CLI users).
"""

from __future__ import annotations

from typing import Any

try:
    # Package context (installed checkout, tests).
    from corpus_kb._setup.validate_configs_toolnames import _auto_approve_errors
except ImportError:  # pragma: no cover - script context (CI validate job)
    import importlib as _importlib

    _toolnames = _importlib.import_module("validate_configs_toolnames")
    _auto_approve_errors = _toolnames._auto_approve_errors


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
        errors.append("Missing required 'mcp' key — OpenCode format uses 'mcp', not 'mcpServers'")
        return errors

    if "mcpServers" in config:
        errors.append("Found 'mcpServers' key — this is the old format; use 'mcp' for OpenCode")

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
                f"{prefix}: 'command' must be an array of strings "
                f"(e.g. ['corpus-kb', '--transport', 'stdio']), "
                f"got {type(command).__name__}"
            )
        elif not command:
            errors.append(f"{prefix}: 'command' array must not be empty")

    # autoApprove must be a non-empty list of valid tool names
    errors.extend(
        _auto_approve_errors(prefix, tool_config.get("autoApprove"), with_valid_list=True)
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
    if command is not None and not isinstance(command, str):
        errors.append(
            f"{prefix}: 'command' must be a string (e.g. 'corpus-kb'), got {type(command).__name__}"
        )

    # autoApprove validation (same as OpenCode)
    errors.extend(
        _auto_approve_errors(prefix, tool_config.get("autoApprove"), with_valid_list=False)
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
    if command is not None and not isinstance(command, str):
        errors.append(
            f"{prefix}: 'command' must be a string (e.g. 'corpus-kb'), got {type(command).__name__}"
        )

    return errors
