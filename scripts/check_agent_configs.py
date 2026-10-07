"""Cross-editor MCP config consistency checker.

Reads the MCP configuration files for OpenCode, Claude Code, Cursor, and
Codex and verifies that every tool defined in one config is also defined in
the others with matching descriptions and command invocations. It ALSO runs
the harness-pack drift gate (``scripts/gen_harness_pack.py --check``) so
registry <-> wrapper <-> config parity fails loudly (todo 18, r7.2/r9).

Usage:
    python scripts/check_agent_configs.py

Exit code 0 = consistent, 1 = inconsistency detected.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_PATHS: dict[str, Path] = {
    "opencode": Path("opencode.json"),
    "claude": Path("mcp-configs/claude-code.json"),
    "cursor": Path("mcp-configs/cursor.json"),
    "codex": Path("mcp-configs/codex.json"),
}

GEN_HARNESS_PACK_REL = Path("scripts") / "gen_harness_pack.py"

# ---------------------------------------------------------------------------
# Config loading and extraction
# ---------------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    """Load and parse a JSON config file."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_tool_map(config: dict[str, Any], format_name: str) -> dict[str, dict[str, Any]]:
    """Return a mapping of tool name -> tool config for a given format."""
    container = config.get("mcp", {}) if format_name == "opencode" else config.get("mcpServers", {})

    if not isinstance(container, dict):
        return {}

    return {
        name: tool_config
        for name, tool_config in container.items()
        if isinstance(tool_config, dict)
    }


def normalize_command(tool_config: dict[str, Any], format_name: str) -> list[str]:
    """Return the full command invocation as a list of strings.

    OpenCode stores the command as an array under the ``command`` key, while
    Claude Code and Cursor split it into ``command`` (string) and ``args``
    (list). This function normalizes both shapes to a single comparable list.
    """
    if format_name == "opencode":
        command = tool_config.get("command", [])
        if isinstance(command, list):
            return [str(part) for part in command]
        return [str(command)]

    command = tool_config.get("command", "")
    args = tool_config.get("args", [])
    parts: list[str] = [str(command)]
    if isinstance(args, list):
        parts.extend(str(arg) for arg in args)
    return parts


# ---------------------------------------------------------------------------
# Consistency checks
# ---------------------------------------------------------------------------


def check_consistency(project_root: Path) -> list[str]:
    """Compare tool names, descriptions, and commands across configs.

    Returns a list of human-readable error strings. An empty list means all
    configs are consistent.
    """
    errors: list[str] = []
    tool_maps: dict[str, dict[str, dict[str, Any]]] = {}

    for format_name, rel_path in CONFIG_PATHS.items():
        path = project_root / rel_path
        if not path.exists():
            errors.append(f"{rel_path}: file not found")
            continue

        try:
            config = load_config(path)
        except json.JSONDecodeError as exc:
            errors.append(f"{rel_path}: invalid JSON ({exc})")
            continue

        tool_maps[format_name] = get_tool_map(config, format_name)

    if len(tool_maps) < 2:
        errors.append("Fewer than two config files could be loaded; nothing to compare")
        return errors

    # --- Same set of tool names -------------------------------------------
    tool_names = {fmt: set(tools.keys()) for fmt, tools in tool_maps.items()}
    reference_fmt = next(iter(tool_names))
    reference_names = tool_names[reference_fmt]

    for fmt, names in tool_names.items():
        if fmt == reference_fmt:
            continue

        missing = reference_names - names
        extra = names - reference_names
        if missing or extra:
            parts: list[str] = []
            if missing:
                parts.append(f"missing: {', '.join(sorted(missing))}")
            if extra:
                parts.append(f"extra: {', '.join(sorted(extra))}")
            errors.append(f"{fmt}: tool set differs from {reference_fmt} — {'; '.join(parts)}")

    # --- Matching descriptions and commands for common tools ---------------
    common_tools = set.intersection(*tool_names.values()) if tool_names else set()

    for tool_name in sorted(common_tools):
        descriptions: dict[str, str] = {}
        commands: dict[str, list[str]] = {}

        for fmt, tools in tool_maps.items():
            tool_config = tools[tool_name]
            descriptions[fmt] = str(tool_config.get("description", ""))
            commands[fmt] = normalize_command(tool_config, fmt)

        unique_descriptions = set(descriptions.values())
        if len(unique_descriptions) > 1:
            details = "; ".join(f"{fmt}: '{desc}'" for fmt, desc in descriptions.items())
            errors.append(f"{tool_name}: description mismatch — {details}")

        unique_commands = {tuple(cmd) for cmd in commands.values()}
        if len(unique_commands) > 1:
            details = "; ".join(f"{fmt}: {cmd!r}" for fmt, cmd in commands.items())
            errors.append(f"{tool_name}: command mismatch — {details}")

    return errors


def run_harness_parity_check(project_root: Path) -> list[str]:
    """Run the generated-harness-pack drift gate (stdlib-only, no deps).

    Loads scripts/gen_harness_pack.py by path — same constraint as the
    agent-config-consistency CI job, which installs NO dependencies.
    """
    path = project_root / GEN_HARNESS_PACK_REL
    if not path.exists():
        return [f"{GEN_HARNESS_PACK_REL}: not found"]
    spec = importlib.util.spec_from_file_location("gen_harness_pack", path)
    if spec is None or spec.loader is None:
        return [f"{GEN_HARNESS_PACK_REL}: cannot load"]
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return list(module.run_harness_check(project_root))
    finally:
        sys.modules.pop(spec.name, None)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the consistency check and exit with an appropriate status code."""
    project_root = Path(__file__).resolve().parent.parent
    errors = check_consistency(project_root)
    errors.extend(f"harness-pack: {e}" for e in run_harness_parity_check(project_root))

    if errors:
        print("Agent config consistency check failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("All agent configs are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
