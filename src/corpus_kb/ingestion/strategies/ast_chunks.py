"""Tree-sitter seam for the code strategy: lazy grammars, byte-span math.

The ``[code]`` extra owns the tree-sitter packages. Every import stays
inside a function (the semchunk pattern), so extras-less installs never
touch the grammars and static tooling never resolves them. A node's byte
offsets map to char offsets on the canonical text through a prefix-sum
table, so chunk spans stay exact on the decoded document text.
"""

from __future__ import annotations

import bisect
import importlib
from typing import Any

# file suffix -> (display language, grammar module, language function).
# tree-sitter-typescript ships two languages (typescript and tsx).
GRAMMARS: dict[str, tuple[str, str, str]] = {
    ".py": ("python", "tree_sitter_python", "language"),
    ".js": ("javascript", "tree_sitter_javascript", "language"),
    ".mjs": ("javascript", "tree_sitter_javascript", "language"),
    ".cjs": ("javascript", "tree_sitter_javascript", "language"),
    ".jsx": ("javascript", "tree_sitter_javascript", "language"),
    ".ts": ("typescript", "tree_sitter_typescript", "language_typescript"),
    ".tsx": ("tsx", "tree_sitter_typescript", "language_tsx"),
    ".rs": ("rust", "tree_sitter_rust", "language"),
    ".go": ("go", "tree_sitter_go", "language"),
    ".java": ("java", "tree_sitter_java", "language"),
}

CODE_SUFFIXES = frozenset(GRAMMARS)


def grammar_for(filename: str) -> tuple[str, str, str] | None:
    """The (language, module, function) grammar triple for a file name."""
    suffix = filename[filename.rfind(".") :].lower() if "." in filename else ""
    return GRAMMARS.get(suffix)


def load_parser(module_name: str, function_name: str) -> Any | None:
    """A ready tree-sitter Parser, or None when the extra is absent.

    Dynamic import on purpose: the extra is optional and CI never installs
    it, so the import edge must stay invisible to static resolution (the
    semchunk/topics pattern).
    """
    try:
        core = importlib.import_module("tree_sitter")
        grammar = importlib.import_module(module_name)
    except ImportError:
        return None
    build_language = getattr(grammar, function_name)
    language = core.Language(build_language())
    return core.Parser(language)


def byte_table(text: str) -> list[int]:
    """Prefix-sum table: char offset -> UTF-8 byte offset."""
    if text.isascii():
        return list(range(len(text) + 1))
    table = [0] * (len(text) + 1)
    total = 0
    for i, char in enumerate(text):
        table[i] = total
        point = ord(char)
        total += 1 if point < 0x80 else 2 if point < 0x800 else 3 if point < 0x10000 else 4
    table[-1] = total
    return table


def char_offset(table: list[int], byte_pos: int) -> int:
    """The char offset for a byte offset, or -1 when it splits a character."""
    index = bisect.bisect_left(table, byte_pos)
    if index < len(table) and table[index] == byte_pos:
        return index
    return -1


def named_children(node: Any) -> list[Any]:
    """The named child nodes of one tree-sitter node (fake-friendly)."""
    children = getattr(node, "named_children", None)
    if children is None:
        children = [child for child in node.children if child.is_named]
    return list(children)


# Node types that always stand alone: one top-level definition is one chunk,
# whatever its size. The markers cover the grammar naming across languages
# (python *._definition, js/ts/go/java *_declaration, rust function_item).
_DEFINITION_MARKERS = ("definition", "declaration")
_DEFINITION_EXTRA_TYPES = frozenset({"function_item", "method_item"})


def is_definition(node: Any) -> bool:
    node_type = str(getattr(node, "type", ""))
    return node_type in _DEFINITION_EXTRA_TYPES or any(
        marker in node_type for marker in _DEFINITION_MARKERS
    )


def group_spans(
    spans: list[tuple[int, int, Any]],
    budget: int,
    end_limit: int | None = None,
) -> list[tuple[int, int, list[Any], bool]]:
    """Greedy adjacent grouping up to the budget (node gaps included).

    Each group is (start, end, nodes, oversized); an oversized group holds
    one node whose span alone exceeds the budget — the caller recurses
    into its children. Groups pack adjacent small nodes, so the slice
    between the group's start and end is one exact, contiguous piece of
    text. A definition node never packs: it always forms its own group.
    Every group extends through the gap up to the next group's start, and
    the last group extends to ``end_limit`` (the parent's end), both
    capped at the budget so a folded gap cannot overflow one chunk.
    """
    groups: list[tuple[int, int, list[Any], bool]] = []
    start: int | None = None
    end = 0
    nodes: list[Any] = []
    for node_start, node_end, node in spans:
        if is_definition(node):
            if start is not None:
                groups.append((start, end, nodes, False))
                start, nodes = None, []
            groups.append((node_start, node_end, [node], node_end - node_start > budget))
        elif node_end - node_start > budget:
            if start is not None:
                groups.append((start, end, nodes, False))
                start, nodes = None, []
            groups.append((node_start, node_end, [node], True))
        elif start is None:
            start, end, nodes = node_start, node_end, [node]
        elif node_end - start > budget:
            groups.append((start, end, nodes, False))
            start, end, nodes = node_start, node_end, [node]
        else:
            end = node_end
            nodes.append(node)
    if start is not None:
        groups.append((start, end, nodes, False))
    for i in range(len(groups) - 1):
        s, e, ns, oversized = groups[i]
        limit = min(groups[i + 1][0], s + budget)
        groups[i] = (s, max(e, limit), ns, oversized)
    if groups and end_limit is not None and end_limit >= 0:
        s, e, ns, oversized = groups[-1]
        groups[-1] = (s, max(e, min(end_limit, s + budget)), ns, oversized)
    return groups
