#!/usr/bin/env python3
"""
Validate OpenCode/MCP configuration files.

Validates:
1. opencode.json (root) against OpenCode native schema
2. mcp-configs/*.json against their respective format schemas
3. Cross-config consistency checks
4. Catches old format errors (e.g., opencode.json.bak with mcpServers)

Exit code 0 = all valid, exit code 1 = one or more violations.
"""

import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

# Valid tool names extracted from src/tools/
VALID_TOOL_NAMES = {
    # search_tools.py
    "search",
    "search_context",
    "search_similar",
    "retrieve_context",
    # ingest_tools.py
    "ingest_file",
    "ingest_text",
    "ingest_directory",
    "list_documents",
    "delete_document",
    # database_tools.py
    "sql_query",
    "sql_execute",
    "sql_tables",
    "add_tag",
    "tag_document",
    "untag_document",
    "get_document_tags",
    "set_metadata",
    "get_metadata",
    "sync_database",
    "query_document_stats",
    # graph_tools.py
    "add_entity",
    "add_relation",
    "search_graph",
    "bfs",
    "get_entity_relations",
    # version_tools.py
    "list_versions",
    "create_tag",
    "get_stats",
    "checkout_version",
    "restore_version",
    "create_branch",
    "list_branches",
    "switch_branch",
}

# ---------------------------------------------------------------------------
# Error accumulator
# ---------------------------------------------------------------------------


class ValidationError:
    """Represents a single validation error."""

    def __init__(self, file_path: str, message: str):
        self.file_path = file_path
        self.message = message

    def __str__(self):
        return f"[{self.file_path}] {self.message}"


errors: list[ValidationError] = []


def error(file_path: str, message: str):
    errors.append(ValidationError(file_path, message))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_json(file_path: Path) -> dict | None:
    """Load and parse a JSON file. Returns None on parse error."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        error(str(file_path), f"Invalid JSON: {e}")
        return None
    except FileNotFoundError:
        error(str(file_path), "File not found")
        return None


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _rel_path(file_path: Path) -> str:
    """Get relative path from REPO_ROOT, or filename if outside repo."""
    try:
        return str(file_path.relative_to(REPO_ROOT))
    except ValueError:
        return file_path.name


def validate_opencode_format(file_path: Path, data: dict):
    """Validate OpenCode native format: mcp key, command as array, etc."""
    rel = _rel_path(file_path)

    # Must have $schema
    if "$schema" not in data:
        error(rel, "Missing '$schema' key")

    # Must have 'mcp' key (not 'mcpServers')
    if "mcpServers" in data:
        error(rel, "Uses old 'mcpServers' format — must use 'mcp' key")
        return  # Can't continue validation with wrong top-level key

    if "mcp" not in data:
        error(rel, "Missing 'mcp' key")
        return

    mcp = data["mcp"]
    if not isinstance(mcp, dict):
        error(rel, "'mcp' must be an object")
        return

    for tool_name, tool_config in mcp.items():
        prefix = f"mcp.{tool_name}"

        # type field
        if "type" not in tool_config:
            error(rel, f"{prefix}: Missing 'type' field")

        # description field
        if "description" not in tool_config:
            error(rel, f"{prefix}: Missing 'description' field")

        # command must be an array
        if "command" not in tool_config:
            error(rel, f"{prefix}: Missing 'command' field")
        elif not isinstance(tool_config["command"], list):
            error(
                rel,
                f"{prefix}: 'command' must be an array, got {type(tool_config['command']).__name__}",
            )

        # environment field
        if "environment" not in tool_config:
            error(rel, f"{prefix}: Missing 'environment' field")
        elif not isinstance(tool_config["environment"], dict):
            error(rel, f"{prefix}: 'environment' must be an object")

        # autoApprove must be a list of valid tool names
        if "autoApprove" not in tool_config:
            error(rel, f"{prefix}: Missing 'autoApprove' field")
        elif not isinstance(tool_config["autoApprove"], list):
            error(rel, f"{prefix}: 'autoApprove' must be a list")
        else:
            for tool in tool_config["autoApprove"]:
                if tool not in VALID_TOOL_NAMES:
                    error(
                        rel,
                        f"{prefix}: autoApprove contains invalid tool name '{tool}'",
                    )


def validate_claude_code_format(file_path: Path, data: dict):
    """Validate Claude Code format: mcpServers, command string, args array, autoApprove."""
    rel = _rel_path(file_path)

    if "mcpServers" not in data:
        error(rel, "Missing 'mcpServers' key (required for Claude Code format)")
        return

    mcp_servers = data["mcpServers"]
    if not isinstance(mcp_servers, dict):
        error(rel, "'mcpServers' must be an object")
        return

    for tool_name, tool_config in mcp_servers.items():
        prefix = f"mcpServers.{tool_name}"

        # command must be a string
        if "command" not in tool_config:
            error(rel, f"{prefix}: Missing 'command' field")
        elif not isinstance(tool_config["command"], str):
            error(
                rel,
                f"{prefix}: 'command' must be a string, got {type(tool_config['command']).__name__}",
            )

        # args must be an array
        if "args" not in tool_config:
            error(rel, f"{prefix}: Missing 'args' field")
        elif not isinstance(tool_config["args"], list):
            error(rel, f"{prefix}: 'args' must be an array")

        # autoApprove must be present and a list
        if "autoApprove" not in tool_config:
            error(rel, f"{prefix}: Missing 'autoApprove' field")
        elif not isinstance(tool_config["autoApprove"], list):
            error(rel, f"{prefix}: 'autoApprove' must be a list")
        else:
            for tool in tool_config["autoApprove"]:
                if tool not in VALID_TOOL_NAMES:
                    error(
                        rel,
                        f"{prefix}: autoApprove contains invalid tool name '{tool}'",
                    )


def validate_cursor_format(file_path: Path, data: dict):
    """Validate Cursor format: mcpServers, command string, args array, no autoApprove."""
    rel = _rel_path(file_path)

    if "mcpServers" not in data:
        error(rel, "Missing 'mcpServers' key (required for Cursor format)")
        return

    mcp_servers = data["mcpServers"]
    if not isinstance(mcp_servers, dict):
        error(rel, "'mcpServers' must be an object")
        return

    for tool_name, tool_config in mcp_servers.items():
        prefix = f"mcpServers.{tool_name}"

        # command must be a string
        if "command" not in tool_config:
            error(rel, f"{prefix}: Missing 'command' field")
        elif not isinstance(tool_config["command"], str):
            error(
                rel,
                f"{prefix}: 'command' must be a string, got {type(tool_config['command']).__name__}",
            )

        # args must be an array
        if "args" not in tool_config:
            error(rel, f"{prefix}: Missing 'args' field")
        elif not isinstance(tool_config["args"], list):
            error(rel, f"{prefix}: 'args' must be an array")


def validate_old_format(file_path: Path, data: dict):
    """
    Validate that an old-format file (like opencode.json.bak) is correctly
    identified as using the deprecated mcpServers format.
    This validator *expects* the old format and reports it as an error
    so CI catches it.
    """
    rel = _rel_path(file_path)

    if "mcpServers" in data:
        error(
            rel,
            "Uses deprecated 'mcpServers' format — this file should be migrated to 'mcp' key or removed",
        )
    elif "mcp" in data:
        # If it was already migrated, that's fine — no error
        pass
    else:
        error(rel, "Missing both 'mcp' and 'mcpServers' keys")


# ---------------------------------------------------------------------------
# Cross-config consistency
# ---------------------------------------------------------------------------


def extract_tool_names_opencode(data: dict) -> set[str]:
    """Extract tool names from OpenCode format config."""
    if "mcp" not in data or not isinstance(data["mcp"], dict):
        return set()
    return set(data["mcp"].keys())


def extract_tool_names_mcp_servers(data: dict) -> set[str]:
    """Extract tool names from mcpServers format config."""
    if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
        return set()
    return set(data["mcpServers"].keys())


def extract_auto_approve_opencode(data: dict) -> dict[str, list]:
    """Extract autoApprove lists from OpenCode format."""
    result = {}
    if "mcp" not in data or not isinstance(data["mcp"], dict):
        return result
    for name, cfg in data["mcp"].items():
        if isinstance(cfg, dict) and "autoApprove" in cfg:
            result[name] = cfg["autoApprove"]
    return result


def extract_auto_approve_mcp_servers(data: dict) -> dict[str, list]:
    """Extract autoApprove lists from mcpServers format."""
    result = {}
    if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
        return result
    for name, cfg in data["mcpServers"].items():
        if isinstance(cfg, dict) and "autoApprove" in cfg:
            result[name] = cfg["autoApprove"]
    return result


def extract_descriptions_opencode(data: dict) -> dict[str, str]:
    """Extract descriptions from OpenCode format."""
    result = {}
    if "mcp" not in data or not isinstance(data["mcp"], dict):
        return result
    for name, cfg in data["mcp"].items():
        if isinstance(cfg, dict) and "description" in cfg:
            result[name] = cfg["description"]
    return result


def extract_descriptions_mcp_servers(data: dict) -> dict[str, str]:
    """Extract descriptions from mcpServers format."""
    result = {}
    if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
        return result
    for name, cfg in data["mcpServers"].items():
        if isinstance(cfg, dict) and "description" in cfg:
            result[name] = cfg["description"]
    return result


def cross_config_consistency(configs: dict[str, dict | None]):
    """
    Run cross-config consistency checks.
    configs: {relative_path: parsed_data}
    """
    # Separate by format
    opencode_configs = {}  # path -> data (uses 'mcp' key)
    mcp_servers_configs = {}  # path -> data (uses 'mcpServers' key)

    for path, data in configs.items():
        if data is None:
            continue
        if "mcp" in data:
            opencode_configs[path] = data
        if "mcpServers" in data:
            mcp_servers_configs[path] = data

    # Collect all tool name sets
    all_tool_sets: dict[str, set[str]] = {}
    for path, data in opencode_configs.items():
        all_tool_sets[path] = extract_tool_names_opencode(data)
    for path, data in mcp_servers_configs.items():
        all_tool_sets[path] = extract_tool_names_mcp_servers(data)

    # Check all configs define the same set of tools
    if len(all_tool_sets) > 1:
        reference_path = None
        reference_tools = None
        for path, tools in all_tool_sets.items():
            if reference_tools is None:
                reference_path = path
                reference_tools = tools
                continue
            if tools != reference_tools:
                missing = reference_tools - tools
                extra = tools - reference_tools
                msg = f"Tool set mismatch vs {reference_path}"
                if missing:
                    msg += f"; missing tools: {missing}"
                if extra:
                    msg += f"; extra tools: {extra}"
                error(path, msg)

    # Check autoApprove lists match where applicable (Cursor excluded)
    auto_approve_maps: dict[str, dict[str, list]] = {}
    for path, data in opencode_configs.items():
        auto_approve_maps[path] = extract_auto_approve_opencode(data)
    for path, data in mcp_servers_configs.items():
        # Skip Cursor format (no autoApprove)
        if "cursor" in path.lower():
            continue
        auto_approve_maps[path] = extract_auto_approve_mcp_servers(data)

    if len(auto_approve_maps) > 1:
        reference_path = None
        reference_aa = None
        for path, aa in auto_approve_maps.items():
            if reference_aa is None:
                reference_path = path
                reference_aa = aa
                continue
            if aa != reference_aa:
                error(
                    path,
                    f"autoApprove mismatch vs {reference_path}",
                )

    # Check descriptions match across configs
    desc_maps: dict[str, dict[str, str]] = {}
    for path, data in opencode_configs.items():
        desc_maps[path] = extract_descriptions_opencode(data)
    for path, data in mcp_servers_configs.items():
        desc_maps[path] = extract_descriptions_mcp_servers(data)

    if len(desc_maps) > 1:
        reference_path = None
        reference_descs = None
        for path, descs in desc_maps.items():
            if reference_descs is None:
                reference_path = path
                reference_descs = descs
                continue
            if descs != reference_descs:
                for tool_name in set(list(descs.keys()) + list(reference_descs.keys())):
                    d1 = reference_descs.get(tool_name)
                    d2 = descs.get(tool_name)
                    if d1 != d2:
                        error(
                            path,
                            f"Description mismatch for tool '{tool_name}' vs {reference_path}",
                        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * 60)
    print("OpenCode/MCP Config Validation")
    print("=" * 60)

    # 1. Validate root opencode.json
    root_config_path = REPO_ROOT / "opencode.json"
    print(f"\nValidating {root_config_path.relative_to(REPO_ROOT)}...")
    root_data = load_json(root_config_path)
    if root_data is not None:
        validate_opencode_format(root_config_path, root_data)

    # 2. Validate mcp-configs/*.json
    mcp_configs_dir = REPO_ROOT / "mcp-configs"
    if mcp_configs_dir.is_dir():
        for config_file in sorted(mcp_configs_dir.glob("*.json")):
            rel_name = config_file.name.lower()
            print(f"\nValidating {config_file.relative_to(REPO_ROOT)}...")
            data = load_json(config_file)
            if data is not None:
                if "opencode" in rel_name:
                    validate_opencode_format(config_file, data)
                elif "claude" in rel_name:
                    validate_claude_code_format(config_file, data)
                elif "cursor" in rel_name:
                    validate_cursor_format(config_file, data)
                else:
                    # Default: try OpenCode format
                    validate_opencode_format(config_file, data)

    # 3. Validate old format files (*.bak)
    for bak_file in REPO_ROOT.glob("*.bak"):
        print(f"\nValidating {bak_file.relative_to(REPO_ROOT)}...")
        data = load_json(bak_file)
        if data is not None:
            validate_old_format(bak_file, data)

    # 4. Cross-config consistency
    print("\nRunning cross-config consistency checks...")
    all_configs: dict[str, dict | None] = {}

    # Root config
    all_configs["opencode.json"] = root_data

    # mcp-configs
    if mcp_configs_dir.is_dir():
        for config_file in sorted(mcp_configs_dir.glob("*.json")):
            all_configs[f"mcp-configs/{config_file.name}"] = load_json(config_file)

    cross_config_consistency(all_configs)

    # 5. Report
    print("\n" + "=" * 60)
    if errors:
        print(f"FAILED — {len(errors)} error(s) found:")
        for e in errors:
            print(f"  {e}")
        print("=" * 60)
        sys.exit(1)
    else:
        print("PASSED — All configs valid.")
        print("=" * 60)
        sys.exit(0)


if __name__ == "__main__":
    main()
