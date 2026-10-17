"""UX Wave 6 example-notebook checks (always run — CI has no marimo).

The notebooks are marimo ``.py`` files served from ``notebooks/``. They
are NOT executed in CI; these tests pin the two contracts the console
and the review depend on: every file compiles as plain Python, and the
imports reach only the standard library, ``corpus_kb``, and ``marimo``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
NOTEBOOKS_DIR = REPO / "notebooks"

ALLOWED_ROOTS = {"corpus_kb", "marimo"} | set(sys.stdlib_module_names)

EXPECTED_NOTEBOOKS = ("eval_report.py", "explore_topics.py", "retrieval_ablation.py")


def _notebook_paths() -> list[Path]:
    return sorted(NOTEBOOKS_DIR.glob("*.py"))


def test_every_expected_example_notebook_is_present() -> None:
    names = {path.name for path in _notebook_paths()}
    missing = [name for name in EXPECTED_NOTEBOOKS if name not in names]
    assert not missing, f"example notebooks missing from notebooks/: {missing}"


@pytest.mark.parametrize("path", _notebook_paths(), ids=lambda p: p.name)
def test_notebook_compiles_as_plain_python(path: Path) -> None:
    compile(path.read_text(encoding="utf-8"), str(path), "exec")


@pytest.mark.parametrize("path", _notebook_paths(), ids=lambda p: p.name)
def test_notebook_imports_stay_in_the_allowed_set(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    unknown = sorted(roots - ALLOWED_ROOTS)
    assert not unknown, f"{path.name} imports outside stdlib/corpus_kb/marimo: {unknown}"


@pytest.mark.parametrize("path", _notebook_paths(), ids=lambda p: p.name)
def test_notebook_is_a_marimo_app(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = [
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    ]
    assert "app" in assignments, f"{path.name} does not define a marimo app"
