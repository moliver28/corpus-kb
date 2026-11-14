"""The autoApprove set-diff seam for agent MCP configs (novice-UX W3c).

Diffs every host's ``autoApprove`` list against the ONE registry-derived
set (``surface_registry.SERVER_AUTO_APPROVE``), loaded by path so this
checker stays stdlib-only (the agent-config-consistency CI job installs
NO dependencies). Split out of ``check_agent_configs.py`` for the
250-line rule; the bodies are byte-identical moves.

autoApprove rules: Cursor's format has no ``autoApprove`` key (its
validator rejects the key), so Cursor is skipped BY RULE. Codex ignores
``autoApprove`` at runtime (audit P1-6); its template still carries the
derived list so the three templates stay byte-identical in shape.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SURFACE_REGISTRY_REL = Path("src") / "corpus_kb" / "surface_registry.py"

# Hosts the autoApprove set-diff may inspect. A host whose envelope has
# no ``autoApprove`` key is skipped by rule (see module docstring), not
# by accident.
AUTO_APPROVE_HOSTS = ("opencode", "claude", "codex")


def load_auto_approve_source(project_root: Path) -> tuple[str, ...] | None:
    """The registry-derived autoApprove list, loaded by path (stdlib only)."""
    path = project_root / SURFACE_REGISTRY_REL
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("corpus_surface_registry", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return tuple(module.SERVER_AUTO_APPROVE)
    finally:
        sys.modules.pop(spec.name, None)


def check_auto_approve_sets(
    project_root: Path, tool_maps: dict[str, dict[str, dict[str, Any]]]
) -> list[str]:
    """Diff each host's autoApprove list against the derived set.

    A missing key on claude/opencode is a shape error the validators
    already catch; here a present-but-different list is the drift. The
    same server name is assumed across hosts (the tool-set check above
    fails first when the names diverge).
    """
    derived = load_auto_approve_source(project_root)
    if derived is None:
        return [f"{SURFACE_REGISTRY_REL}: not found (cannot diff autoApprove sets)"]
    derived_set = set(derived)
    errors: list[str] = []
    for fmt in AUTO_APPROVE_HOSTS:
        tools = tool_maps.get(fmt, {})
        for server in tools.values():
            present = server.get("autoApprove")
            if not isinstance(present, list):
                continue
            present_set = set(present)
            missing = derived_set - present_set
            extra = present_set - derived_set
            if missing or extra:
                parts: list[str] = []
                if missing:
                    parts.append(f"missing: {', '.join(sorted(missing))}")
                if extra:
                    parts.append(f"extra: {', '.join(sorted(extra))}")
                errors.append(
                    f"{fmt}: autoApprove differs from the registry-derived set "
                    f"({len(derived_set)} tools) — {'; '.join(parts)}"
                )
            if len(present) != len(present_set):
                errors.append(f"{fmt}: autoApprove carries duplicate entries")
    return errors
