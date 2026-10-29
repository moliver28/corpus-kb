"""DuckDB FTS index lifecycle (storage portability program §13, sp-duckdb-search).

The DuckDB fts index never auto-updates: rows written after
``create_fts_index`` stay invisible to ``match_bm25`` (W0 spike 6,
findings/search-fts.md). This manager enforces a COUNT watermark at search
time: the ``chunks`` row count must equal the index's ``docs`` row count,
else the index is dropped and rebuilt (drop is free; rebuild is roughly
0.5 s per 5000 short chunks). The watermark catches inserts, deletes and
supersede waves — the pipeline supersedes rather than edits, so an
in-place text change with a stable count is not a shape it produces.

DuckDB 1.4 decisions recorded here:

- ``match_bm25`` is a scalar macro in schema ``fts_main_chunks``; the index
  tables persist in the file, so queries work after reopen without a LOAD.
- CONJUNCTIVE MODE STAYS OFF. duckdb-fts issue #15 is present in 1.4.5: a
  conjunctive query that contains a stopword always returns zero rows,
  because the macro counts unfiltered tokens against stopword-filtered
  index entries. The lexical arm sanitizes and stays disjunctive.
- ``LOAD fts`` is attempted best-effort before the first create; a missing
  extension surfaces from the CREATE itself, never from the LOAD.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

#: The schema the fts extension builds for the chunks index
#: (``fts_main_`` + table name, W0 spike 1/6).
FTS_SCHEMA = "fts_main_chunks"


async def ensure_fts_index(gateway: Any) -> bool:
    """Rebuild the fts index when it is missing or stale. True when rebuilt.

    The watermark is the row count of ``chunks`` against the index's
    ``docs`` table. A missing index reads as zero rows. Only a missing
    catalog entry triggers the rebuild path silently; every other read
    error propagates (a locked file must fail loudly, not rebuild).
    """
    import duckdb

    total = await gateway.fetchval("SELECT count(*) FROM chunks")
    try:
        indexed = await gateway.fetchval(f"SELECT count(*) FROM {FTS_SCHEMA}.docs")
    except duckdb.CatalogException:
        indexed = None
    if indexed is not None and int(indexed) == int(total or 0):
        return False
    await rebuild_fts_index(gateway)
    return True


async def rebuild_fts_index(gateway: Any) -> None:
    """Drop and recreate the fts index over chunks(chunk_id, text).

    FTS indexes have no ALTER on DuckDB (W0 spike 6): rebuild means
    DROP + CREATE. A missing index makes the DROP raise CatalogException;
    that state is the CREATE's own precondition, so it is suppressed.
    """
    import duckdb

    with suppress(Exception):  # autoload may have it already; the CREATE is the arbiter
        await gateway.execute("LOAD fts")
    with suppress(duckdb.CatalogException):
        await gateway.execute("PRAGMA drop_fts_index('main.chunks')")
    await gateway.execute("PRAGMA create_fts_index('main.chunks', 'chunk_id', 'text')")
