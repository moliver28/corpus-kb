"""Safe SQL text for grammar positions that cannot take bind parameters.

DuckDB and Postgres DDL, ``PRAGMA``, ``ATTACH`` and ``SET`` grammar need
string literals and identifiers; placeholders are rejected there (duckdb
discussion #14830). Values for those positions pass through this module
first: shape validation, then standard quoting. Callers interpolate only
repo-owned names or operator configuration; no end-user input reaches
these positions.
"""

from __future__ import annotations

import re

_IDENTIFIER_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def check_identifier(name: str) -> str:
    """Return ``name`` when it is a plain SQL identifier; raise otherwise."""
    if not _IDENTIFIER_RX.fullmatch(name):
        raise ValueError(f"not a plain SQL identifier: {name!r}")
    return name


def sql_identifier(name: str) -> str:
    """Validate ``name`` and wrap it in double quotes for SQL."""
    return '"' + check_identifier(name) + '"'


def quoted_identifier(name: str) -> str:
    """Quote any identifier with SQL quote doubling (introspected names)."""
    if not name or "\x00" in name:
        raise ValueError(f"not a usable SQL identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def sql_qualified(name: str) -> str:
    """Validate a dot-separated name and quote each part."""
    return ".".join(sql_identifier(part) for part in name.split("."))


def sql_escape_identifier(name: str) -> str:
    """Validate ``name``, then escape it as a double-quoted identifier.

    Same text as ``sql_identifier``: shape check first, then standard
    double-quote escaping. The distinct name states the act at call
    sites where an escaped identifier lands in a statement.
    """
    return sql_identifier(name)


def sql_literal(value: str) -> str:
    """Escape ``value`` as a single-quoted SQL string literal."""
    if "\x00" in value:
        raise ValueError("SQL string literal contains a NUL byte")
    return "'" + value.replace("'", "''") + "'"
