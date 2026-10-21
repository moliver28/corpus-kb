"""DuckDB FTS index lifecycle for the research read models (sp-research-port).

Mirrors ``storage/duckdb_fts.py`` for the two retrieval-child tables: the
fts index never auto-updates (W0 spike 6), so search time enforces a COUNT
watermark per table — the table's row count must equal the index's ``docs``
count, else the index is dropped and rebuilt. NULL ``qa_text`` rows index
with length zero and score NULL, which the lexical arm drops.

- ``match_bm25`` is a scalar macro in schema ``fts_main_<table>``; the
  ``docname`` argument is the table's id column value (W1-verified: probe
  by id value scores, everything else returns NULL).
- CONJUNCTIVE MODE STAYS OFF and queries are sanitized by the caller
  (duckdb-fts issue #15: a stopword in a conjunctive query always returns
  zero rows).
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

#: (table, id column, text column) — the retrieval children, in arm order.
_FTS_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("research_units", "unit_id", "text"),
    ("research_exchanges", "exchange_id", "qa_text"),
)


def fts_schema(table: str) -> str:
    """The schema the fts extension builds for one table (``fts_main_`` + name)."""
    return f"fts_main_{table}"


async def ensure_research_fts(gateway: Any) -> bool:
    """Rebuild every missing or stale research fts index. True when rebuilt.

    A missing index reads as zero rows. Only a missing catalog entry takes
    the rebuild path silently; every other read error propagates (a locked
    file must fail loudly, not rebuild).
    """
    import duckdb

    rebuilt = False
    for table, id_col, text_col in _FTS_TARGETS:
        total = await gateway.fetchval(f"SELECT count(*) FROM {table}")
        try:
            indexed = await gateway.fetchval(f"SELECT count(*) FROM {fts_schema(table)}.docs")
        except duckdb.CatalogException:
            indexed = None
        if indexed is not None and int(indexed) == int(total or 0):
            continue
        await rebuild_research_fts(gateway, table, id_col, text_col)
        rebuilt = True
    return rebuilt


async def rebuild_research_fts(gateway: Any, table: str, id_col: str, text_col: str) -> None:
    """Drop and recreate one fts index (no ALTER exists on DuckDB)."""
    import duckdb

    with suppress(Exception):  # autoload may have it already; the CREATE is the arbiter
        await gateway.execute("LOAD fts")
    with suppress(duckdb.CatalogException):
        await gateway.execute(f"PRAGMA drop_fts_index('main.{table}')")
    await gateway.execute(f"PRAGMA create_fts_index('main.{table}', '{id_col}', '{text_col}')")
