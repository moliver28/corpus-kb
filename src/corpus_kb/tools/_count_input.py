"""Shared window-count guard for the window reads (Wave-5 review P2).

THE RULE: a window read's ``limit`` is a whole number of at least 1,
its ``offset`` a whole number of at least 0, and both stay inside the
Postgres int8 bind range. A break raises the taxonomy
``ValidationError``: the socket answers -32602, the MCP tool call fails
as an ``is_error`` result (the installed SDK drops the message itself —
the parity test pins this), the CLI exits 1, and an HTTP route that
calls the guard answers 400 — never a raw database ``LIMIT`` error.

The rule covers a surface only where that surface's path calls this
guard. The true split, read by read:

- Document chunks: all four surfaces. The tool calls the guard for
  MCP, socket, and CLI. The HTTP route calls it directly.
- Relations: MCP, socket, and CLI, through ``relations_list``. The HTTP
  route builds its query with ``opt_int``, so HTTP accepts ``limit=0``
  and answers an empty window.
- Entities: the guard is not on this path. MCP passes ``limit`` raw.
  Socket and HTTP coerce it with ``opt_int``. All three accept
  ``limit=0``.

The CLI's typer options (``--limit`` min 1, ``--offset`` min 0) reject
the same values one step earlier on all three reads. On the entity
read they are the only check.
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
