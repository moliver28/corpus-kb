"""psycopg adapters for the migrate legs (storage-portability program §3.3).

psycopg's stubs type ``Cursor.execute`` as ``LiteralString``-only, while
the legs build SQL with lib-owned, ``_table_guard``-checked table names
inside runtime strings. :class:`PgCursor` is the one plain-``str`` trust
boundary for that SQL: the cast to psycopg's ``Query`` type lives only
here, and the cursor still closes on context exit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import psycopg
    from psycopg.abc import QueryNoTemplate


class PgCursor:
    """Plain-str psycopg cursor surface (the stubs demand LiteralString)."""

    def __init__(self, cur: psycopg.Cursor[Any]) -> None:
        self._cur = cur

    def __enter__(self) -> PgCursor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._cur.close()

    def execute(self, query: str, params: Sequence[object] | None = None) -> None:
        self._cur.execute(
            cast("QueryNoTemplate", query),
            params,
        )

    def executemany(self, query: str, params_seq: Sequence[Sequence[object]]) -> None:
        self._cur.executemany(
            cast("QueryNoTemplate", query),
            params_seq,
        )

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._cur.fetchone()

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._cur.fetchall())
