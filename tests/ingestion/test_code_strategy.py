"""Code strategy (unified-ingestion Wave 5): AST chunks, fallback, wiring.

Offline: suffix detection, fake-tree AST chunking (packing, recursion,
hard-split), the honest no-extra/no-grammar fallback to the default plan
with a warnings entry, file sources, and a REAL tree-sitter leg that skips
when the ``[code]`` extra is absent (CI never installs it).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.ingestion.base import (
    DETECT_ADOPT_CONFIDENCE,
    TOKEN_BUDGET_CHARS,
    SourceProfile,
    validate_plan,
)
from corpus_kb.ingestion.registry import resolve_strategy, strategy_names
from corpus_kb.ingestion.strategies.code import CODE_STRATEGY_NAME, CodeStrategy
from corpus_kb.ingestion.strategies.default import DefaultStrategy

CFG_EMPTY: dict[str, object] = {"ingestion": {"strategies": {}, "default": "default"}}

PYTHON_SOURCE = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"

_MONKEYPARSE_TARGET = "corpus_kb.ingestion.strategies.code.load_parser"


def _profile(path: str = "script.py", is_file: bool = False) -> SourceProfile:
    return SourceProfile(source_type="code", is_file=is_file, path=path)


class _Node:
    """A minimal tree-sitter node double (byte offsets into UTF-8 bytes)."""

    def __init__(
        self, node_type: str, start_byte: int, end_byte: int, children: list[Any] | None = None
    ) -> None:
        self.type = node_type
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.named_children = children or []


class _Tree:
    def __init__(self, root: _Node) -> None:
        self.root_node = root


class _Parser:
    def __init__(self, root: _Node) -> None:
        self._root = root

    def parse(self, data: bytes) -> _Tree:
        assert isinstance(data, bytes)
        return _Tree(self._root)


def _parser_factory(root: _Node) -> Callable[[str, str], _Parser]:
    """A ``load_parser`` double bound to one fake root (same two-arg call)."""

    def factory(module_name: str, function_name: str) -> _Parser:
        return _Parser(root)

    return factory


def _byte_span(text: str, marker: str) -> tuple[int, int]:
    """Byte offsets of the FIRST ``marker`` occurrence inside ``text``."""
    start = text.find(marker)
    return len(text[:start].encode("utf-8")), len(text[: start + len(marker)].encode("utf-8"))


def _two_functions_root(text: str) -> _Node:
    first = _byte_span(text, "def f():\n    return 1")
    second = _byte_span(text, "def g():\n    return 2")
    return _Node(
        "module",
        0,
        len(text.encode("utf-8")),
        [
            _Node("function_definition", first[0], first[1]),
            _Node("function_definition", second[0], second[1]),
        ],
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detect_code_suffix_adopts() -> None:
    for name in ("script.py", "app.ts", "main.rs", "server.go", "Main.java", "index.tsx"):
        result = CodeStrategy().detect("text", name, "")
        assert result.confidence >= DETECT_ADOPT_CONFIDENCE, name
        assert result.reason == "code-suffix"


def test_detect_plain_text_is_zero() -> None:
    assert CodeStrategy().detect("text", "notes.txt", "def main():\n").confidence == 0.0


def test_detect_hint_alone_stays_below_adopt() -> None:
    result = CodeStrategy().detect("code", "notes.txt", "")
    assert result.reason == "source-type-hint"
    assert result.confidence < DETECT_ADOPT_CONFIDENCE


def test_resolution_claims_code_files() -> None:
    resolution = resolve_strategy("code", "script.py", PYTHON_SOURCE, CFG_EMPTY)
    assert resolution.strategy.name == CODE_STRATEGY_NAME
    assert resolution.basis == "detection"


def test_strategy_is_registered() -> None:
    assert CODE_STRATEGY_NAME in strategy_names()


# ---------------------------------------------------------------------------
# AST chunking with a fake parser (the packing/recursion logic, no extra)
# ---------------------------------------------------------------------------


def test_ast_chunks_one_per_top_level_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _two_functions_root(PYTHON_SOURCE)
    monkeypatch.setattr(_MONKEYPARSE_TARGET, _parser_factory(root))
    plan = CodeStrategy().build_chunks(PYTHON_SOURCE, "script.py", _profile())
    assert [chunk.text for chunk in plan.chunks] == [
        "def f():\n    return 1\n\n\n",
        "def g():\n    return 2\n",
    ]
    assert all(chunk.metadata["language"] == "python" for chunk in plan.chunks)
    assert all(chunk.metadata["node_type"] == "function_definition" for chunk in plan.chunks)
    assert all(chunk.source_type == "code" for chunk in plan.chunks)
    for chunk in plan.chunks:
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
    assert validate_plan(plan) == []


def test_ast_chunks_pack_small_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "".join(f"x{i} = {i}\n" for i in range(6))
    spans = [_byte_span(text, line) for line in text.splitlines(keepends=True)]
    root = _Node(
        "module",
        0,
        len(text.encode("utf-8")),
        [_Node("expression_statement", s, e) for s, e in spans],
    )
    monkeypatch.setattr(_MONKEYPARSE_TARGET, _parser_factory(root))
    plan = CodeStrategy().build_chunks(text, "small.py", _profile())
    assert len(plan.chunks) == 1
    assert plan.chunks[0].text == text
    assert validate_plan(plan) == []


def test_ast_chunks_recurse_into_oversized_definitions(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gap between the two methods exceeds the budget, so packing cannot
    # merge them; the class alone exceeds it, so the walk recurses.
    text = (
        "class Big:\n    def a(self):\n        pass\n"
        + "\n" * TOKEN_BUDGET_CHARS
        + "    def b(self):\n        pass\n"
    )
    method_a = _byte_span(text, "def a(self):\n        pass")
    method_b = _byte_span(text, "def b(self):\n        pass")
    big = _Node(
        "class_definition",
        0,
        len(text.encode("utf-8")),
        [
            _Node("function_definition", method_a[0], method_a[1]),
            _Node("function_definition", method_b[0], method_b[1]),
        ],
    )
    root = _Node("module", 0, len(text.encode("utf-8")), [big])
    monkeypatch.setattr(_MONKEYPARSE_TARGET, _parser_factory(root))
    plan = CodeStrategy().build_chunks(text, "big.py", _profile())
    assert len(plan.chunks) == 2
    assert all(c.metadata["node_type"] == "function_definition" for c in plan.chunks)
    for chunk in plan.chunks:
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
    assert validate_plan(plan) == []


def test_oversized_leaf_hard_splits_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "y = " + "1" * (TOKEN_BUDGET_CHARS + 100) + "\n"
    size = len(text.encode("utf-8"))
    root = _Node("module", 0, size, [_Node("assignment", 0, size)])
    monkeypatch.setattr(_MONKEYPARSE_TARGET, _parser_factory(root))
    plan = CodeStrategy().build_chunks(text, "leaf.py", _profile())
    assert len(plan.chunks) > 1
    assert any("hard-split" in warning for warning in plan.warnings)
    for chunk in plan.chunks:
        assert len(chunk.text) <= TOKEN_BUDGET_CHARS
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
    assert validate_plan(plan) == []


# ---------------------------------------------------------------------------
# Honest fallbacks (extra missing, grammar missing, not code)
# ---------------------------------------------------------------------------


def test_missing_extra_falls_back_to_default_plan_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MONKEYPARSE_TARGET, lambda module, function: None)
    plan = CodeStrategy().build_chunks(PYTHON_SOURCE, "script.py", _profile())
    assert plan.strategy == CODE_STRATEGY_NAME
    assert any("'code' extra" in warning for warning in plan.warnings)
    default = DefaultStrategy().build_chunks(PYTHON_SOURCE, "script.py", _profile())
    assert [c.text for c in plan.chunks] == [c.text for c in default.chunks]
    assert validate_plan(plan) == []


def test_missing_grammar_falls_back_with_warning() -> None:
    plan = CodeStrategy().build_chunks("plain text", "notes.txt", _profile("notes.txt"))
    assert plan.strategy == CODE_STRATEGY_NAME
    assert any("no tree-sitter grammar" in warning for warning in plan.warnings)
    assert validate_plan(plan) == []


def test_empty_text_is_empty_but_conformant(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _Node("module", 0, 0)
    monkeypatch.setattr(_MONKEYPARSE_TARGET, _parser_factory(root))
    plan = CodeStrategy().build_chunks("", "script.py", _profile())
    assert plan.chunks == []
    assert plan.empty_reason == "no source text"
    assert validate_plan(plan) == []


def test_no_code_structure_is_empty_but_conformant(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _Node("module", 0, 4)
    monkeypatch.setattr(_MONKEYPARSE_TARGET, _parser_factory(root))
    plan = CodeStrategy().build_chunks("data", "script.py", _profile())
    assert plan.chunks == []
    assert plan.empty_reason == "no top-level code structure found"
    assert validate_plan(plan) == []


def test_file_source_reads_from_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "script.py"
    path.write_text(PYTHON_SOURCE, encoding="utf-8")
    root = _two_functions_root(PYTHON_SOURCE)
    monkeypatch.setattr(_MONKEYPARSE_TARGET, _parser_factory(root))
    plan = CodeStrategy().build_chunks(
        "", "script.py", SourceProfile(source_type="code", is_file=True, path=str(path))
    )
    assert len(plan.chunks) == 2
    for chunk in plan.chunks:
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
    assert validate_plan(plan) == []


# ---------------------------------------------------------------------------
# Real tree-sitter leg (skips without the [code] extra)
# ---------------------------------------------------------------------------


def test_real_tree_sitter_python_chunks() -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_python")
    plan = CodeStrategy().build_chunks(PYTHON_SOURCE, "script.py", _profile())
    assert plan.chunks
    assert {chunk.metadata["language"] for chunk in plan.chunks} == {"python"}
    for chunk in plan.chunks:
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
    assert validate_plan(plan) == []
