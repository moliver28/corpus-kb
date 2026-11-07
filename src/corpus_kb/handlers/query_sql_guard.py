"""The read-only statement gate for the SQL query route (issue #349).

``POST /api/query/sql`` must refuse every statement that is not a read,
on both engines. One gate runs BEFORE any connection opens, so a refused
statement never changes a row and the typed ``WriteRefusedError`` reaches
every surface with its one status/code/exit triple.

Two layers, because the dependency rules forbid a shared SQL parser in
``src/`` (sqlglot is dev-only per deptry; duckdb is an optional extra):

1. :func:`refuse_non_read` — stdlib-only, runs on EVERY call. It masks
   the opaque regions (string literals, comments, quoted identifiers,
   dollar quotes), then proves the statement is a read: exactly one
   statement, a read keyword first, and no write keyword anywhere
   outside the masked regions. A write hidden in a CTE (``WITH x AS
   (DELETE ...) SELECT ...``) trips the scan; a write word inside a
   string or comment does not.
2. :func:`refuse_writes_duckdb` — the DuckDB arm's second net. DuckDB's
   own parser (``extract_statements``) must count one statement and type
   it ``SELECT``; anything it cannot parse is refused too. The fail-safe
   rule throughout: a statement is allowed only when the gate can prove
   it is a read.
"""

from __future__ import annotations

import re

from corpus_kb.errors import WriteRefusedError

#: The words that may start a read statement (``(`` covers parenthesized
#: reads such as ``(SELECT 1) UNION (SELECT 2)``).
_READ_HEADS = frozenset({"(", "SELECT", "WITH", "TABLE", "VALUES", "SHOW", "EXPLAIN"})

#: The words that mark a write wherever they appear outside masked
#: regions. Data-modifying CTEs hide DELETE/INSERT/UPDATE/MERGE behind a
#: WITH; ``FOR UPDATE`` hides a row lock behind a SELECT; SELECT INTO
#: hides a CREATE TABLE; EXPLAIN ANALYZE hides ANALYZE; nextval/setval
#: advance a sequence. Every entry is a statement keyword, so no
#: corpus-kb column name trips the scan.
_WRITE_KEYWORD_RE = re.compile(
    r"\b(DELETE|INSERT|UPDATE|MERGE|TRUNCATE|CREATE|DROP|ALTER|GRANT|REVOKE"
    r"|ANALYZE|INTO|NEXTVAL|SETVAL)\b",
    re.IGNORECASE,
)

_DOLLAR_QUOTE_RE = re.compile(r"\$([A-Za-z_][A-Za-z_0-9]*)?\$")

_EMPTY_MESSAGE = "The SQL route needs one read statement."
_MULTI_MESSAGE = "The SQL route accepts one statement per call."
_READS_ONLY_MESSAGE = "The SQL route accepts read statements only."
_UNPROVEN_MESSAGE = "The SQL route could not prove this statement is a read."


def refuse_non_read(sql: str) -> None:
    """Refuse anything that is not exactly one provable read statement."""
    masked = _mask_opaque(sql)
    statements = [part for part in masked.split(";") if part.strip()]
    if not statements:
        raise WriteRefusedError(_EMPTY_MESSAGE)
    if len(statements) > 1:
        raise WriteRefusedError(_MULTI_MESSAGE)
    _refuse_non_read_body(statements[0])


def refuse_writes_duckdb(sql: str) -> None:
    """DuckDB's own parser must agree: one statement, typed SELECT."""
    import duckdb

    try:
        statements = duckdb.extract_statements(sql)
    except Exception as exc:
        raise WriteRefusedError(_UNPROVEN_MESSAGE) from exc
    if len(statements) != 1:
        raise WriteRefusedError(_MULTI_MESSAGE)
    if statements[0].type != duckdb.StatementType.SELECT:
        raise WriteRefusedError(_READS_ONLY_MESSAGE)


def _refuse_non_read_body(statement: str) -> None:
    """Refuse one masked statement unless its head and body prove a read."""
    stripped = statement.lstrip()
    head = "(" if stripped.startswith("(") else ""
    if not head:
        match = re.match(r"[A-Za-z]+", stripped)
        head = match.group(0).upper() if match else ""
    if not head:
        raise WriteRefusedError(_EMPTY_MESSAGE)
    if head not in _READ_HEADS:
        raise WriteRefusedError(f"{_READS_ONLY_MESSAGE} Found: {head}.")
    write = _WRITE_KEYWORD_RE.search(statement)
    if write is not None:
        raise WriteRefusedError(f"{_READS_ONLY_MESSAGE} Found: {write.group(0)}.")


def _mask_opaque(sql: str) -> str:
    """Blank literals, comments, and quoted identifiers, preserving length.

    Every masked region is opaque: SQL words inside it are data, never
    statements. A doubled quote is an escape inside a region, never its
    end. An unterminated region consumes the tail (the statement is then
    refused by its head, never half-run). Length preservation lets the
    caller split statements on the masked text while the original keeps
    its byte offsets.
    """
    out: list[str] = []
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]
        start = i
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = length if end == -1 else end + 1
        elif sql.startswith("/*", i):
            i = _skip_block_comment(sql, i)
        elif ch in ("'", '"', "`"):
            i = _skip_quoted(sql, i, ch)
        elif ch == "$" and (quote := _DOLLAR_QUOTE_RE.match(sql, i)) is not None:
            tag = quote.group(0)
            end = sql.find(tag, i + len(tag))
            i = length if end == -1 else end + len(tag)
        else:
            out.append(ch)
            i += 1
            continue
        out.append(" " * (i - start))
    return "".join(out)


def _skip_block_comment(sql: str, i: int) -> int:
    """One past the end of the block comment at ``i`` (Postgres nests them)."""
    i += 2
    depth = 1
    length = len(sql)
    while i < length and depth:
        if sql.startswith("/*", i):
            depth += 1
            i += 2
        elif sql.startswith("*/", i):
            depth -= 1
            i += 2
        else:
            i += 1
    return i


def _skip_quoted(sql: str, i: int, quote: str) -> int:
    """One past the end of the quoted region at ``i`` ('' escapes inside)."""
    i += 1
    length = len(sql)
    while i < length:
        if sql.startswith(quote * 2, i):
            i += 2
        elif sql[i] == quote:
            return i + 1
        else:
            i += 1
    return length
