"""Unit tests for the SQL identifier/literal helpers (issues #205, #206).

These helpers guard the grammar positions where DuckDB and Postgres take
literals, not bind parameters (ATTACH, PRAGMA, DDL identifiers).
"""

from __future__ import annotations

import pytest

from corpus_kb.storage.sql_ident import (
    check_identifier,
    quoted_identifier,
    sql_identifier,
    sql_literal,
    sql_qualified,
)


def test_check_identifier_accepts_plain_names() -> None:
    assert check_identifier("chunks") == "chunks"
    assert check_identifier("_x1") == "_x1"


@pytest.mark.parametrize("bad", ["", "1x", "a-b", "a b", 'a"b', "a;b", "a.b", "main.chunks"])
def test_check_identifier_rejects_other_shapes(bad: str) -> None:
    with pytest.raises(ValueError, match="not a plain SQL identifier"):
        check_identifier(bad)


def test_sql_identifier_quotes() -> None:
    assert sql_identifier("chunks") == '"chunks"'


def test_quoted_identifier_doubles_quotes() -> None:
    assert quoted_identifier('we"ird') == '"we""ird"'
    with pytest.raises(ValueError, match="not a usable SQL identifier"):
        quoted_identifier("")


def test_sql_qualified_quotes_each_part() -> None:
    assert sql_qualified("main.chunks") == '"main"."chunks"'
    with pytest.raises(ValueError, match="not a plain SQL identifier"):
        sql_qualified("main.bad-name")


def test_sql_literal_doubles_and_refuses_nul() -> None:
    assert sql_literal("it's") == "'it''s'"
    with pytest.raises(ValueError, match="NUL"):
        sql_literal("a" + chr(0) + "b")
