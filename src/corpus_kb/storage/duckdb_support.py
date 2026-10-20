"""DuckDB-side support helpers for the SqlGateway drivers.

Pure shaping and mapping logic with no connection state: DSN conversion
(program rule 13), connect-error mapping (rule 11), PG-format status-tag
synthesis (``projections/research/_common.py`` parses these strings), and
the append-only table guard (DuckDB has no triggers; sp-coding-port owns
edits to the list). Split from the driver class to honor the 250-line
module limit (program rule 7).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias
from urllib.parse import unquote, urlparse

import asyncpg

from corpus_kb.errors import ConflictError, DatabaseUnavailableError

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
