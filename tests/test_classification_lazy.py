"""The lazy-import contract for the classification family (P0 fix).

An extras-less install must be able to import every catalogue handler
(the MCP server resolves them eagerly at startup). So sklearn and
threadpoolctl may appear only INSIDE function bodies in these modules,
and the head tier's gates must answer the typed dependency error, never
a bare ModuleNotFoundError.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

LAZY_MODULES = (
    "classification/tier_a.py",
    "classification/calibrate.py",
    "classification/tier_b.py",
)
_BLOCKED = ("sklearn", "threadpoolctl")
_SRC = Path(__file__).resolve().parent.parent / "src" / "corpus_kb"


def _top_level_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


@pytest.mark.parametrize("relative", LAZY_MODULES)
def test_sklearn_imports_are_function_local(relative: str) -> None:
    top = _top_level_imports(_SRC / relative)
    roots = {name.split(".")[0] for name in top}
    assert not roots & set(_BLOCKED), f"{relative} imports {_BLOCKED} at module level: {top}"


def test_tool_modules_import_without_sklearn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry resolves handler strings at startup: keep them importable."""
    for name in list(sys.modules):
        if name.split(".")[0] in _BLOCKED:
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _guard(name: str, *args: object, **kwargs: object):
        if name.split(".")[0] in _BLOCKED:
            raise ModuleNotFoundError(f"blocked for the test: {name}")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("builtins.__import__", _guard)
    for module in (
        "corpus_kb.tools.classification_tools",
        "corpus_kb.classification.predict",
        "corpus_kb.classification.tier_a",
        "corpus_kb.classification.calibrate",
        "corpus_kb.classification.tier_b",
    ):
        importlib.import_module(module)


def test_head_gates_answer_the_typed_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.classification import tier_a
    from corpus_kb.errors import DependencyMissingError

    monkeypatch.setitem(sys.modules, "sklearn", None)
    monkeypatch.setitem(sys.modules, "threadpoolctl", None)
    with pytest.raises(DependencyMissingError):
        tier_a.ensure_sklearn()
    with pytest.raises(DependencyMissingError):
        tier_a.thread_pin()
    with pytest.raises(DependencyMissingError):
        tier_a.sklearn_version()
    with pytest.raises(DependencyMissingError):
        tier_a.head_estimator()
