"""SQL text utilities for the schema-parity gate.

Statement splitting is dialect-neutral and dollar-quote aware. It exists
because migration files mix DDL with DO blocks, and drift lines must
point at real SQL lines.
"""

from __future__ import annotations

import re


class SchemaParityError(Exception):
    """The gate cannot fold one migration file. The message has file:line."""


_DOLLAR = re.compile(r"(\$[A-Za-z_]*\$)")


def _skip_leading(sql: str, start: int) -> int:
    """Advance past whitespace and comments so line numbers hit real SQL."""
    i = start
    while i < len(sql):
        two = sql[i : i + 2]
        if two == "--":
            end = sql.find("\n", i)
            i = len(sql) if end == -1 else end + 1
        elif two == "/*":
            end = sql.find("*/", i + 2)
            i = len(sql) if end == -1 else end + 2
        elif sql[i] in " \t\r\n":
            i += 1
        else:
            break
    return i


def split_statements(sql: str) -> list[tuple[int, str]]:
    """Split SQL into (start offset, text) pairs.

    Skips ';' inside -- comments, block comments, '...' strings, and
    $tag$ dollar blocks. The offset points at the first SQL token, so
    line numbers do not count the comment block above a statement.
    Adapted from findings/probes/sqlglot_audit.py.
    """
    out: list[tuple[int, str]] = []
    start = 0
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        two = sql[i : i + 2]
        if two == "--":
            end = sql.find("\n", i)
            i = n if end == -1 else end + 1
        elif two == "/*":
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif c == "'":
            i += 1
            while i < n and sql[i] != "'":
                i += 2 if sql[i : i + 2] == "''" else 1
            i += 1
        elif m := _DOLLAR.match(sql, i):
            end = sql.find(m.group(1), m.end())
            i = n if end == -1 else end + len(m.group(1))
        elif c == ";":
            out.append((_skip_leading(sql, start), sql[start:i]))
            start = i = i + 1
        else:
            i += 1
    trimmed = _skip_leading(sql, start)
    if sql[trimmed:].strip():
        out.append((trimmed, sql[trimmed:]))
    return out
