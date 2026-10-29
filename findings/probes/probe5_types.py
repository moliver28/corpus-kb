# pyright: basic
"""S3 spike 5: type-check surface probe (duckdb typing, ~30 lines)."""

from __future__ import annotations

import duckdb
import re


def _ident(name: str) -> str:
    """Pass plain SQL identifiers; refuse everything else."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise SystemExit(f"unsafe SQL identifier: {name!r}")
    return name


def open_db(path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(path, read_only=True)


def count_rows(con: duckdb.DuckDBPyConnection, table: str) -> int:
    cur: duckdb.DuckDBPyConnection = con.cursor()
    try:
        statements = {"count": "SELECT count(*) FROM " + _ident(table)}
        cur.execute(statements["count"])
        rows: list[tuple] = cur.fetchall()
        (n,) = rows[0]
        return int(n)
    finally:
        cur.close()


def peek(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple]:
    result = con.execute(sql)
    return result.fetchall()


def wrong_returns_something(conn: duckdb.DuckDBPyConnection) -> None:
    # deliberate type error: fetchall() is not an int
    x: int = conn.execute("SELECT 1").fetchall()
    print(x)


def main() -> None:
    con = open_db(":memory:")
    con.execute("CREATE TABLE t (i INTEGER)")
    con.execute("INSERT INTO t VALUES (1), (2), (3)")
    print(count_rows(con, "t"), peek(con, "SELECT * FROM t ORDER BY i"))
    con.close()


if __name__ == "__main__":
    wrong_returns_something(duckdb.connect(":memory:"))
