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
    """Set each dot-path key in the raw YAML text; keep every other byte."""
    lines = text.splitlines(keepends=True)
    for dot_path in sorted(updates):
        block, _, key = dot_path.partition(".")
        lines = _set_block_key(lines, block, key, updates[dot_path])
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
    header = re.compile(rf"^{re.escape(block)}:[ \t]*(#.*)?$")
    head_idx = next((i for i, ln in enumerate(lines) if header.match(ln.rstrip("\n"))), None)
    token = _scalar_token(value)
    if head_idx is None:
        result = list(lines)
        if result and not result[-1].endswith("\n"):
            result[-1] += "\n"
        result.append(f"{block}:\n  {key}: {token}\n")
        return result
    end = _block_end(lines, head_idx)
    key_re = re.compile(rf"^( +){re.escape(key)}:[ \t]*.*?$")
    for i in range(head_idx + 1, end):
        line = lines[i].rstrip("\n")
        match = key_re.match(line)
        if match:
            comment = _trailing_comment(line)
            newline = lines[i][len(line) :]
            return [
                *lines[:i],
                f"{match.group(1)}{key}: {token}{comment}{newline}",
                *lines[i + 1 :],
            ]
    indent = _member_indent(lines, head_idx, end)
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
