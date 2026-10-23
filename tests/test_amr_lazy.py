"""The lazy-import contract for the AMR op modules (the P0 rule).

An extras-less install must import every handler the MCP server
resolves eagerly. So sklearn, threadpoolctl, and ollama may appear only
INSIDE function bodies in these modules, and the seams they use must
answer the typed dependency error, never a bare ModuleNotFoundError.
Modeled on ``tests/test_classification_lazy.py``.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

AMR_MODULES = (
    "tools/keyphrase_tools.py",
    "tools/keyphrase_llm.py",
    "tools/lexical_tools.py",
    "tools/matrix_tools.py",
    "tools/sentiment_tools.py",
    "tools/active_learn_tools.py",
)
# The AST check blocks every heavy import, including ollama. The import
# guard blocks the OPTIONAL extras only: ollama is a base dependency,
# and the pre-existing ingest_common chain imports it transitively at
# module level (outside this program's file ownership).
_BLOCKED = ("sklearn", "threadpoolctl", "ollama", "bertopic", "umap", "hdbscan", "torch")
_GUARDED = ("sklearn", "threadpoolctl", "bertopic", "umap", "hdbscan", "torch")
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


@pytest.mark.parametrize("relative", AMR_MODULES)
def test_heavy_imports_are_function_local(relative: str) -> None:
    top = _top_level_imports(_SRC / relative)
    roots = {name.split(".")[0] for name in top}
    assert not roots & set(_BLOCKED), f"{relative} imports {_BLOCKED} at module level: {top}"


def test_amr_tool_modules_import_without_the_heavy_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry resolves handler modules at startup: keep them importable."""
    for name in list(sys.modules):
        if name.split(".")[0] in _GUARDED:
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _guard(name: str, *args: object, **kwargs: object):
        if name.split(".")[0] in _GUARDED:
            raise ModuleNotFoundError(f"blocked for the test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _guard)
    for module in (
        "corpus_kb.tools.keyphrase_tools",
        "corpus_kb.tools.keyphrase_llm",
        "corpus_kb.tools.lexical_tools",
        "corpus_kb.tools.matrix_tools",
        "corpus_kb.tools.sentiment_tools",
        "corpus_kb.tools.active_learn_tools",
    ):
        importlib.import_module(module)


async def test_lexical_gate_answers_the_typed_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.classification import tier_a
    from corpus_kb.errors import DependencyMissingError
    from corpus_kb.tools import lexical_tools

    monkeypatch.setitem(sys.modules, "sklearn", None)
    monkeypatch.setitem(sys.modules, "threadpoolctl", None)
    with pytest.raises(DependencyMissingError):
        tier_a.ensure_sklearn()
    # The op raises the same typed error before any probing.
    with pytest.raises(DependencyMissingError):
        await lexical_tools.lexical_classify(object(), config=None, tenant_id="t")
