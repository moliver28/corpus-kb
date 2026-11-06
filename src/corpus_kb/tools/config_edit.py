"""Targeted YAML text edits: set dot-path keys, keep every other byte.

The config.yaml file is user-owned and hand-commented, so a rewrite that
dumps the parsed tree would destroy comments. This module does the
smallest possible text edit instead: one line replaced, inserted, or one
block appended. Everything else stays byte-identical.

Callers: the calibrate apply op (``tools.calibrate_tools``, issue #200).
Keys are dot-paths (``retrieval.rrf_k``) with values the recipes
allow-list already vetted, so every value is a simple scalar.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import yaml

__all__ = ["apply_keys_to_text", "write_atomic"]


def write_atomic(config_path: Path, text: str) -> None:
    """Write through a tmp file, so a crash never leaves a half file."""
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(config_path)


def apply_keys_to_text(text: str, updates: Mapping[str, object]) -> str:
    """Set each dot-path key in the raw YAML text; keep every other byte.

    Two-level keys (``analysis.routing.margin``) edit inside the nested
    block, creating it when missing. Deeper chains have no allow-listed
    key today and refuse loudly instead of guessing.
    """
    lines = text.splitlines(keepends=True)
    for dot_path in sorted(updates):
        parts = dot_path.split(".")
        if len(parts) == 2:
            lines = _set_block_key(lines, parts[0], parts[1], updates[dot_path])
        elif len(parts) == 3:
            lines = _set_nested_key(lines, parts[0], parts[1], parts[2], updates[dot_path])
        else:
            raise ValueError(f"config_edit: unsupported dot path {dot_path!r}.")
    return "".join(lines)


def _scalar_token(value: object) -> str:
    """One YAML scalar token for a config value line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    dumped = yaml.safe_dump(value, default_flow_style=True)
    return str(dumped).splitlines()[0]


def _set_block_key(lines: list[str], block: str, key: str, value: object) -> list[str]:
    """One key set inside one top-level block: replace, insert, or append."""
    head_idx = _block_header(lines, block)
    if head_idx is None:
        result = list(lines)
        if result and not result[-1].endswith("\n"):
            result[-1] += "\n"
        result.append(f"{block}:\n  {key}: {_scalar_token(value)}\n")
        return result
    end = _block_end(lines, head_idx)
    return _set_key_in_block(lines, head_idx, end, key, value)


def _set_nested_key(lines: list[str], top: str, mid: str, key: str, value: object) -> list[str]:
    """One leaf inside a two-level chain: replace, insert, or build it.

    The walk reuses the top-level helpers at each level: a missing
    top block gains the whole chain; a missing mid block grows inside
    the top block; an existing mid block takes the edit in place. The
    mid header must sit at the top block's member column, so a block
    of the same name nested DEEPER elsewhere never hijacks the edit.
    """
    token = _scalar_token(value)
    head_idx = _block_header(lines, top)
    if head_idx is None:
        result = list(lines)
        if result and not result[-1].endswith("\n"):
            result[-1] += "\n"
        result.extend([f"{top}:\n", f"  {mid}:\n", f"    {key}: {token}\n"])
        return result
    end = _block_end(lines, head_idx)
    mid_idx = _child_header(lines, head_idx, end, mid)
    if mid_idx is None:
        indent = _member_indent(lines, head_idx, end)
        return [
            *lines[:end],
            f"{indent}{mid}:\n{indent}  {key}: {token}\n",
            *lines[end:],
        ]
    mid_end = _child_block_end(lines, mid_idx, end)
    return _set_key_in_block(lines, mid_idx, mid_end, key, value)


def _block_header(lines: list[str], block: str) -> int | None:
    """The line index of one top-level ``block:`` header, or None."""
    header = re.compile(rf"^{re.escape(block)}:[ \t]*(#.*)?$")
    return next((i for i, ln in enumerate(lines) if header.match(ln.rstrip("\n"))), None)


def _child_header(lines: list[str], header: int, end: int, name: str) -> int | None:
    """The ``name:`` member header at the block's member column, or None.

    The pattern anchors to the FIRST member's exact indent, so a same
    named block nested deeper inside another child never matches.
    """
    member_indent = _member_indent(lines, header, end)
    child = re.compile(rf"^{member_indent}{re.escape(name)}:[ \t]*(#.*)?$")
    return next(
        (i for i in range(header + 1, end) if child.match(lines[i].rstrip("\n"))),
        None,
    )


def _child_block_end(lines: list[str], header: int, limit: int) -> int:
    """The line after one nested block's members (indent-aware).

    The walk stops at the first non-blank line indented no deeper than
    the header itself, so a sibling of the nested block ends it.
    """
    head_indent = len(lines[header]) - len(lines[header].lstrip(" "))
    index = header + 1
    while index < limit:
        stripped = lines[index].rstrip("\n")
        if stripped.strip():
            indent = len(stripped) - len(stripped.lstrip(" "))
            if indent <= head_indent:
                break
        index += 1
    return index


def _set_key_in_block(
    lines: list[str], head_idx: int, end: int, key: str, value: object
) -> list[str]:
    """One key set inside one already-located block: replace or insert.

    The key pattern anchors to the block's member column, so a deeper
    same-named key inside a grandchild block never matches first.
    """
    token = _scalar_token(value)
    indent = _member_indent(lines, head_idx, end)
    key_re = re.compile(rf"^{indent}{re.escape(key)}:[ \t]*.*?$")
    for i in range(head_idx + 1, end):
        line = lines[i].rstrip("\n")
        if key_re.match(line):
            comment = _trailing_comment(line)
            newline = lines[i][len(line) :]
            return [
                *lines[:i],
                f"{indent}{key}: {token}{comment}{newline}",
                *lines[i + 1 :],
            ]
    return [*lines[:end], f"{indent}{key}: {token}\n", *lines[end:]]


def _block_end(lines: list[str], head_idx: int) -> int:
    """The line after the block's members (the next column-0 content line)."""
    end = head_idx + 1
    while end < len(lines):
        line = lines[end].rstrip("\n")
        if line.strip() and not line[0].isspace():
            break
        end += 1
    return end


def _member_indent(lines: list[str], head_idx: int, end: int) -> str:
    """The indent of the block's first real member line (two spaces fallback)."""
    for i in range(head_idx + 1, end):
        line = lines[i].rstrip("\n")
        if line.strip() and not line.lstrip().startswith("#"):
            return line[: len(line) - len(line.lstrip())]
    return "  "


def _trailing_comment(line: str) -> str:
    """The `` # ...`` suffix of a config line, or the empty string."""
    match = re.search(r"\s+#", line)
    return line[match.start() :] if match else ""
