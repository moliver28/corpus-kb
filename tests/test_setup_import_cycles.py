"""Regression pin for issue #472: the _setup module family stays acyclic.

CodeQL counts every import statement as a dependency edge. Module level,
function level, and TYPE_CHECKING guards all count. This test mirrors that
with :mod:`ast`, so a re-introduced cycle fails here before CodeQL runs.

The smoke tests import each previously cyclic pair in a fresh interpreter,
one process per order. A partial-module failure only shows on a fresh
import, so in-process re-imports would not pin the order.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

SETUP_DIR = Path(__file__).resolve().parent.parent / "src" / "corpus_kb" / "_setup"

#: One pair per cycle family that issue #472 broke. Each is imported in
#: both orders in fresh interpreters. Add a pair when you break a cycle.
PREVIOUSLY_CYCLIC_PAIRS: list[tuple[str, str]] = [
    ("install", "doctor_report"),
    ("install", "setup_pull_pair"),
    ("install", "setup_pulls"),
    ("install", "setup_stack"),
    ("install", "setup_steps"),
    ("install", "setup_duckdb"),
    ("doctor_research", "doctor_env"),
    ("doctor_research", "doctor_reranker"),
]


def _module_edges() -> dict[str, set[str]]:
    """Map each _setup module to the _setup modules it imports, all scopes."""
    modules = {p.stem for p in SETUP_DIR.glob("*.py")}
    edges: dict[str, set[str]] = {}
    for path in sorted(SETUP_DIR.glob("*.py")):
        name = "corpus_kb._setup" if path.stem == "__init__" else f"corpus_kb._setup.{path.stem}"
        targets: set[str] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("corpus_kb._setup"):
                        targets.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    if node.module == "corpus_kb._setup":
                        # `from corpus_kb._setup import setup_stack` binds the
                        # submodule, so the edge goes to the submodule.
                        for alias in node.names:
                            if alias.name in modules:
                                targets.add(f"corpus_kb._setup.{alias.name}")
                    elif node.module.startswith("corpus_kb._setup"):
                        targets.add(node.module)
                elif node.level > 0:
                    base = "corpus_kb._setup"
                    if node.module:
                        targets.add(f"{base}.{node.module}")
                    else:
                        for alias in node.names:
                            if alias.name in modules:
                                targets.add(f"{base}.{alias.name}")
        edges[name] = targets
    return edges


def _strongly_connected_components(edges: dict[str, set[str]]) -> list[set[str]]:
    """Tarjan SCC over the import graph (iterative; no recursion limit)."""
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[set[str]] = []
    counter = 0

    for root in sorted(edges):
        if root in index_of:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index_of[root] = lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in edges:
                    continue
                if child not in index_of:
                    index_of[child] = lowlink[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(edges.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                component: set[str] = set()
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.add(member)
                    if member == node:
                        break
                components.append(component)
    return components


def _describe_cycles(edges: dict[str, set[str]], components: list[set[str]]) -> str:
    """Render each cyclic component with its internal edges."""
    lines = []
    for component in sorted(components, key=sorted):
        lines.append("cycle among: " + ", ".join(sorted(component)))
        for member in sorted(component):
            for target in sorted(edges.get(member, ()) & component):
                lines.append(f"  {member} -> {target}")
    return "\n".join(lines)


def test_setup_import_graph_has_no_cycles() -> None:
    """Every strongly connected component in _setup is a single module."""
    edges = _module_edges()
    components = _strongly_connected_components(edges)
    cyclic = [c for c in components if len(c) > 1]
    assert not cyclic, f"import cycles found:\n{_describe_cycles(edges, cyclic)}"


def test_setup_modules_import_in_both_orders() -> None:
    """Each previously cyclic pair imports clean in a fresh interpreter."""
    src_dir = str(SETUP_DIR.parent.parent.parent)
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}" if existing else src_dir
    for first, second in PREVIOUSLY_CYCLIC_PAIRS:
        for order in ((first, second), (second, first)):
            code = f"import corpus_kb._setup.{order[0]}\nimport corpus_kb._setup.{order[1]}\n"
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
                check=False,
            )
            assert result.returncode == 0, (
                f"import order {order[0]} -> {order[1]} failed:\n{result.stdout}\n{result.stderr}"
            )
