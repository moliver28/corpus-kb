"""DuckDB-side support helpers for the SqlGateway drivers.

Pure shaping and mapping logic with no connection state: DSN conversion
(program rule 13), connect-error mapping (rule 11), PG-format status-tag
synthesis (``projections/research/_common.py`` parses these strings), the
append-only table guard, and the ``corpus.``-qualification guard (DuckDB
has no triggers there; sp-coding-port owns edits to the append-only list).
Split from the driver class to honor the 250-line module limit (program
rule 7).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias
from urllib.parse import unquote, urlparse

import asyncpg

from corpus_kb.errors import ConflictError, DatabaseUnavailableError, ValidationError

if TYPE_CHECKING:  # function-local at runtime (program rule 9: deptry-visible)
    import duckdb

#: The gateway row type: asyncpg's Record on Postgres, key-and-position
#: dicts on DuckDB. Lives here so both drivers and the re-exports in
#: ``gateway.py`` share one definition without an import cycle.
Row: TypeAlias = asyncpg.Record | dict[str, Any]

MAX_TRANSACTION_ATTEMPTS = 5
_BACKOFF_SECONDS = 0.025
_MAX_PLACEHOLDERS = 2000

#: Append-only tables on DuckDB (no triggers there). Mirrors the PG
#: BEFORE UPDATE/DELETE triggers on the coding and exact-cache tables.
#: sp-coding-port owns edits to this list (program §13).
APPEND_ONLY_TABLES: frozenset[str] = frozenset(
    {
        "chunk_codes",
        "chunk_signals",
        "codebook_releases",
        "coding_gate_decisions",
        "research_exact_cache",
    }
)

_LOCKED_REMEDIATION = "Another program is using this database file. Close it, then try again."

#: Bank string (program doc §11.15). Cited verbatim by the Wave D/E port docs.
_CORPUS_QUALIFIED_MESSAGE = "Use unqualified table names on DuckDB. Remove the corpus. prefix."

_CORPUS_QUALIFIED_RE = re.compile(r"\bcorpus\s*\.", re.IGNORECASE)


def duckdb_path_to_conn_string(path: str | Path) -> str:
    """Convert an OS path to a ``duckdb:///`` connection string."""
    return f"duckdb:///{str(path).replace(chr(92), '/')}"  # chr(92) = backslash


def duckdb_conn_string_to_path(conn_string: str) -> str:
    """Convert a ``duckdb:///`` connection string to a plain OS path.

    DuckDB does not parse URLs and would treat the whole string as a file
    name, so the scheme is stripped here. Accepts a plain path too.
    """
    normalized = conn_string.replace("\\", "/")
    if "://" not in normalized:
        return normalized
    parsed = urlparse(normalized)
    if parsed.scheme != "duckdb" or parsed.netloc:
        raise ValueError(f"Unsupported connection string scheme: {parsed.scheme!r}")
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:(/|$)", path):
        path = path[1:]  # Windows drive letter: strip the URL's leading slash
    while path.startswith("//"):
        path = path[1:]
    return path


def map_connect_error(exc: Exception) -> Exception:
    """Map DuckDB connect failures onto the corpus error taxonomy.

    Concrete types first: ``IOException`` and ``ConnectionException``
    subclass ``OperationalError``, so a broad catch would misfile them.
    """
    import duckdb

    text = str(exc)
    concrete = isinstance(exc, (duckdb.IOException, duckdb.ConnectionException))
    locked = concrete and (
        "being used by another process" in text
        or "File is already open in" in text
        or ("Cannot open database" in text and "read-only mode" in text)
        or "different configuration" in text
    )
    if locked:
        return DatabaseUnavailableError(text, remediation=_LOCKED_REMEDIATION)
    return exc


def _mask_literals(sql: str) -> str:
    """Blank single-quoted strings and double-quoted identifiers.

    Both regions are opaque; a doubled quote is an escape inside the
    region, never its end. An unterminated literal just consumes the tail.
    """
    out: list[str] = []
    i = 0
    quote = ""
    length = len(sql)
    while i < length:
        ch = sql[i]
        if quote:
            if ch == quote:
                if i + 1 < length and sql[i + 1] == quote:
                    i += 2  # escaped quote stays inside the literal
                    continue
                quote = ""
            out.append(" ")
        elif ch in ("'", '"'):
            quote = ch
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _reject_corpus_qualified(sql: str) -> None:
    """Reject ``corpus.``-qualified SQL on the DuckDB path — typed, no rewrite.

    DuckDB names the catalog after the database file stem, so
    ``corpus.t`` only works on a file named ``corpus.duckdb`` and fails
    with a raw catalog error on any other name. The migration mirror
    flattens all tables to ``main``, so ported call sites pass bare
    names. Text rewriting is forbidden: it would corrupt literal data
    (program decision 2026-10-20). Postgres SQL keeps ``corpus.``.
    """
    if _CORPUS_QUALIFIED_RE.search(_mask_literals(sql)):
        raise ValidationError(
            _CORPUS_QUALIFIED_MESSAGE,
            remediation="Remove the corpus. prefix from the SQL. Then run it again.",
        )


def _is_count_result(description: Sequence[Sequence[Any]]) -> bool:
    return len(description) == 1 and description[0][0] == "Count"


def _status_tag(sql: str, conn: duckdb.DuckDBPyConnection) -> str:
    """Synthesize the PG-format tag from the executed statement's result."""
    verb = _leading_word(sql)
    if verb in {"CREATE", "DROP", "ALTER"}:
        return _two_word_tag(sql, verb)
    if verb not in {"INSERT", "UPDATE", "DELETE", "SELECT"}:
        return verb or "OK"
    description = conn.description
    rows = conn.fetchall()
    if description is not None and _is_count_result(description) and rows:
        count = int(rows[0][0])
    else:
        count = len(rows)
    return f"INSERT 0 {count}" if verb == "INSERT" else f"{verb} {count}"


def _leading_word(sql: str) -> str:
    match = re.match(r"^\s*(?:--[^\n]*\n|\s)*([A-Za-z]+)", sql)
    return match.group(1).upper() if match else ""


def _two_word_tag(sql: str, verb: str) -> str:
    """PG spells DDL tags with the object kind: CREATE TABLE, DROP INDEX."""
    rest = sql[sql.upper().find(verb) + len(verb) :]
    second = rest.strip().split(" ", 1)[0] if rest.strip() else ""
    return f"{verb} {second.upper()}" if second else verb


def _check_append_only(sql: str) -> None:
    match = re.match(
        r"\s*(?:--[^\n]*\n)*\s*(?:UPDATE|DELETE)\s+(?:ONLY\s+)?(?:FROM\s+)?"
        r"([A-Za-z_][A-Za-z_0-9]*)",
        sql,
        re.IGNORECASE,
    )
    if match and match.group(1).lower() in APPEND_ONLY_TABLES:
        table = match.group(1).lower()
        raise ConflictError(
            f"{table} is append-only: UPDATE and DELETE are not permitted",
            remediation="Write new rows instead. Existing rows cannot be changed.",
        )


class _Row(dict[str, Any]):
    """Result row as a dict that also reads positionally, like ``Record``."""

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)
