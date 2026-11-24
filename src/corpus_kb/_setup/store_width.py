"""Shared store-width helpers: migration runners and the boot guard.

The migration DDL carries the placeholder ``__EMBEDDING_DIMS__`` where the
primary embedding width (``embedding.dimensions``) belongs. Both runners
substitute it before they apply a file, then record the width they applied
in the store meta table; the boot guard re-checks that row at startup —
issue #559 (G28): a config width that does not match the stored vectors
now refuses at boot instead of at the first ingest write.

One module owns the placeholder, the substitution, the width probes, and
the meta-row access for both engines, so the runner and the guard cannot
drift. DuckDB tables live unqualified in ``main``: a ``corpus.<table>``
spell is ambiguous on a file named ``corpus.duckdb`` (the migration-001
rule), while Postgres keeps the ``corpus`` schema spell.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from corpus_kb.dimensions import EMBEDDING_DIMENSIONS

if TYPE_CHECKING:
    import asyncpg
    import duckdb

#: The placeholder the migration DDL carries instead of a hard-coded width.
EMBEDDING_DIMS_PLACEHOLDER = "__EMBEDDING_DIMS__"

#: The meta key that records the width the runner applied.
EMBEDDING_WIDTH_KEY = "embedding_width"

#: The width every pre-parameterization store was built with: the DDL was
#: hard-coded before this module existed, so an absent meta row means this.
LEGACY_STORE_WIDTH = EMBEDDING_DIMENSIONS

_META_CREATE_PG = (
    "CREATE TABLE IF NOT EXISTS corpus.meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
)
_META_CREATE_DUCK = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"

_WIDTH_TYPE = re.compile(r"(?:halfvec|vector|FLOAT)\s*[\[(]\s*(\d+)\s*[\])]", re.IGNORECASE)


def substitute_dims(sql_text: str, embedding_dims: int) -> str:
    """Replace every width placeholder with ``embedding_dims``."""
    return sql_text.replace(EMBEDDING_DIMS_PLACEHOLDER, str(embedding_dims))


def configured_embedding_dims() -> int:
    """The configured primary width (``embedding.dimensions``).

    Reads the live config, so the ``CORPUS_KB_EMBEDDING_DIMENSIONS`` env
    override flows. Falls back to the shipped default when the section or
    the key is missing or not an int.
    """
    from corpus_kb.config import load_config  # lazy: keeps this module leaf-cheap

    embedding = load_config().get("embedding")
    if isinstance(embedding, dict) and isinstance(embedding.get("dimensions"), int):
        return int(embedding["dimensions"])
    return LEGACY_STORE_WIDTH


def parse_width(type_text: str) -> int | None:
    """The width in ``halfvec(2560)`` / ``FLOAT[2560]`` / ``vector(1024)``.

    Answers None for any other spelling, so callers skip the write instead
    of recording a guessed number.
    """
    match = _WIDTH_TYPE.search(type_text)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# The live column width (what the store actually stores)
# ---------------------------------------------------------------------------


async def store_embedding_width_pg(conn: asyncpg.Connection) -> int | None:
    """The live width of ``chunks_vectors.embedding`` on Postgres."""
    col_type = await conn.fetchval(
        "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
        "WHERE a.attrelid = to_regclass('public.chunks_vectors') "
        "AND a.attname = 'embedding'"
    )
    return parse_width(str(col_type)) if col_type else None


def store_embedding_width_duck_sync(con: duckdb.DuckDBPyConnection) -> int | None:
    """The live width of ``chunks_vectors.embedding`` on DuckDB."""
    rows = con.execute(
        "SELECT data_type FROM duckdb_columns() "
        "WHERE table_name = 'chunks_vectors' AND column_name = 'embedding'"
    ).fetchall()
    return parse_width(str(rows[0][0])) if rows else None


# ---------------------------------------------------------------------------
# The meta row (what the runner recorded)
# ---------------------------------------------------------------------------


async def read_meta_width_pg(conn: asyncpg.Connection) -> str | None:
    """The recorded ``embedding_width`` value, or None when absent."""
    try:
        value = await conn.fetchval(
            "SELECT value FROM corpus.meta WHERE key = $1", EMBEDDING_WIDTH_KEY
        )
    except asyncpg.UndefinedTableError:
        return None
    return str(value) if value is not None else None


def read_meta_width_duck_sync(con: duckdb.DuckDBPyConnection) -> str | None:
    """The recorded ``embedding_width`` value, or None when absent."""
    import duckdb

    try:
        row = con.execute("SELECT value FROM meta WHERE key = ?", [EMBEDDING_WIDTH_KEY]).fetchone()
    except duckdb.CatalogException:
        return None
    return str(row[0]) if row else None


async def write_meta_width_pg(conn: asyncpg.Connection, width: int) -> None:
    """Create the meta table if needed, then record ``width`` (upsert)."""
    await conn.execute(_META_CREATE_PG)
    await conn.execute(
        "INSERT INTO corpus.meta (key, value) VALUES ($1, $2) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        EMBEDDING_WIDTH_KEY,
        str(width),
    )


def write_meta_width_duck_sync(con: duckdb.DuckDBPyConnection, width: int) -> None:
    """Create the meta table if needed, then record ``width`` (upsert)."""
    con.execute(_META_CREATE_DUCK)
    con.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [EMBEDDING_WIDTH_KEY, str(width)],
    )


# ---------------------------------------------------------------------------
# Runner-side recording: observe the live width, then record it
# ---------------------------------------------------------------------------


async def record_applied_width_pg(conn: asyncpg.Connection) -> int | None:
    """Record the width this run left ``chunks_vectors`` with (Postgres).

    The observed catalog width, not the configured one: the base schema
    file shapes fresh stores on Postgres, so the recorded number stays
    honest even when a configured width never reached the columns.
    Answers the recorded width, or None when no width column exists.
    """
    width = await store_embedding_width_pg(conn)
    if width is not None:
        await write_meta_width_pg(conn, width)
    return width


def record_applied_width_duck_sync(con: duckdb.DuckDBPyConnection) -> int | None:
    """Record the width this run left ``chunks_vectors`` with (DuckDB)."""
    width = store_embedding_width_duck_sync(con)
    if width is not None:
        write_meta_width_duck_sync(con, width)
    return width


# ---------------------------------------------------------------------------
# Boot-guard plumbing: probe + lazy backfill on a store file
# ---------------------------------------------------------------------------


def probe_store_width_duck_sync(path: str) -> tuple[str | None, int | None]:
    """(recorded meta width, live column width) from a read-only connection.

    A missing file answers (None, None).
    """
    import duckdb

    if not Path(path).exists():
        return None, None
    con = duckdb.connect(path, read_only=True)
    try:
        stored = read_meta_width_duck_sync(con)
        live = store_embedding_width_duck_sync(con)
    finally:
        con.close()
    return stored, live


def backfill_store_width_duck_sync(path: str, width: int) -> bool:
    """Record ``width`` in the meta table; False when the write fails.

    Opens its own read-write connection: a locked file (another writer
    holds the store) skips the backfill without failing the boot check,
    and the next boot retries.
    """
    import duckdb

    try:
        con = duckdb.connect(path)
    except duckdb.Error:
        return False
    try:
        write_meta_width_duck_sync(con, width)
    except duckdb.Error:
        return False
    finally:
        con.close()
    return True


__all__ = [
    "EMBEDDING_DIMS_PLACEHOLDER",
    "EMBEDDING_WIDTH_KEY",
    "LEGACY_STORE_WIDTH",
    "backfill_store_width_duck_sync",
    "configured_embedding_dims",
    "parse_width",
    "probe_store_width_duck_sync",
    "read_meta_width_duck_sync",
    "read_meta_width_pg",
    "record_applied_width_duck_sync",
    "record_applied_width_pg",
    "store_embedding_width_duck_sync",
    "store_embedding_width_pg",
    "substitute_dims",
    "write_meta_width_duck_sync",
    "write_meta_width_pg",
]
