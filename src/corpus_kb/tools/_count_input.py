"""Shared window-count guard for the chunk read (Wave-5 review P2).

THE CANONICAL RULE, one rule for every surface: a window read's
``limit`` is a whole number of at least 1, its ``offset`` a whole
number of at least 0, and both stay inside the Postgres int8 bind
range. A break raises the taxonomy ``ValidationError``, so HTTP
answers 400, socket and MCP answer -32602, and the CLI exits 1 —
never a raw database ``LIMIT`` error.

The CLI's typer options (``--limit`` min 1, ``--offset`` min 0) reject
the same values one step earlier; this guard is the backstop that
keeps all four surfaces (HTTP, socket, MCP, CLI) on the one rule.
"""

from __future__ import annotations

from corpus_kb.errors import ValidationError
from corpus_kb.utils.whole_int import INT8_MAX, coerce_whole_int

__all__ = ["limit_arg", "offset_arg"]


def limit_arg(raw: object, default: int = 100) -> int:
    """One window's row cap: whole, at least 1, inside int8."""
    value = coerce_whole_int(raw, default, name="limit")
    if value is None or value < 1:
        raise ValidationError("limit must be at least 1.")
    if value > INT8_MAX:
        raise ValidationError("limit is out of range.")
    return value


def offset_arg(raw: object, default: int = 0) -> int:
    """One window's skip: whole, at least 0, inside int8."""
    value = coerce_whole_int(raw, default, name="offset")
    if value is None or value < 0:
        raise ValidationError("offset must not be negative.")
    if value > INT8_MAX:
        raise ValidationError("offset is out of range.")
    return value
