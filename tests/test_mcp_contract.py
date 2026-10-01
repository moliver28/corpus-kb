"""Snapshot contract test for MCP tool wrapper modules.

Discovers every ``src/corpus_kb/tools/*_tools.py`` module, imports it, and
records the public callable names together with their parameter names/kinds.
This guards against accidental renames, argument additions, or signature
changes in the MCP surface.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any


def _discover_tool_module_names() -> list[str]:
    """Return sorted dotted module names for all ``*_tools.py`` modules."""
    repo_root = Path(__file__).parent.parent
    tools_dir = repo_root / "src" / "corpus_kb" / "tools"
    module_names = [f"corpus_kb.tools.{path.stem}" for path in sorted(tools_dir.glob("*_tools.py"))]
    return module_names


def _build_tool_contract() -> dict[str, dict[str, dict[str, str]]]:
    """Build a deterministic, sorted contract of public tool signatures."""
    contract: dict[str, dict[str, dict[str, str]]] = {}

    for module_name in _discover_tool_module_names():
        module = importlib.import_module(module_name)
        callables: dict[str, dict[str, str]] = {}

        for name, obj in inspect.getmembers(module, callable):
            if name.startswith("_"):
                continue
            # Only snapshot callables defined in this module, not imports.
            if getattr(obj, "__module__", None) != module_name:
                continue

            try:
                signature = inspect.signature(obj)
            except (ValueError, TypeError):
                continue

            parameters: dict[str, str] = {
                param_name: param.kind.name for param_name, param in signature.parameters.items()
            }
            callables[name] = parameters

        contract[module_name] = dict(sorted(callables.items()))

    return dict(sorted(contract.items()))


def test_mcp_tool_contract(snapshot: Any) -> None:
    """Public tool callables and their parameter kinds must match snapshot."""
    assert _build_tool_contract() == snapshot
