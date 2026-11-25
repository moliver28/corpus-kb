"""The events tables presence probe and the CREATE_TABLE flag it feeds.

Eventsourcing creates its own tables when ``CREATE_TABLE`` is truthy, so a
static "y" re-runs DDL on every boot (F-10). The boot reads the flag once:
"n" only when every probed table exists, "y" otherwise. Any probe error
fails open — create-if-missing is harmless, a false "n" is not.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import asyncpg


def events_table_names() -> tuple[str, str]:
    """The lib-owned table names, derived from the application class name."""
    from corpus_kb.domain.application import CorpusApplication

    prefix = str(CorpusApplication.name).lower()
    return (f"{prefix}_events", f"{prefix}_snapshots")


async def events_tables_present(conn_str: str, backend: str) -> bool:
    """True only when every probed table exists; False only when one is missing.

    Any probe error RAISES. The caller owns the fail-open decision. The
    probe costs one connection with a 3-second timeout per call.
    """
    names = events_table_names()
    if backend == "duckdb" or conn_str.startswith("duckdb:///"):
        return _sidecar_tables_present(conn_str, names)
    conn = await asyncpg.connect(conn_str, timeout=3)
    try:
        row = await conn.fetchrow(
            f"SELECT to_regclass('public.{names[0]}') AS events,"
            f"to_regclass('public.{names[1]}') AS snapshots"
        )
    finally:
        await conn.close()
    return bool(row) and bool(row["events"]) and bool(row["snapshots"])


def _sidecar_tables_present(conn_str: str, names: tuple[str, str]) -> bool:
    """Check the SQLite sidecar for both tables; missing file means fresh."""
    import sqlite3

    from corpus_kb.storage.duckdb_support import events_sidecar_path

    path = Path(events_sidecar_path(conn_str))
    if not path.exists():
        return False
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)", names
        ).fetchall()
    finally:
        con.close()
    return set(names).issubset({str(row[0]) for row in rows})


def _run_probe(coro: Coroutine[Any, Any, bool]) -> bool:
    """Run one async probe with or without a running loop in this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def events_create_table_flag(conn_str: str, backend: str) -> str:
    """\"y\" unless every event table is proven present; probe errors fail open.

    Called once per app construction; ``get_app`` caches the app, so the
    3-second probe cost lands once per process per DSN.
    """
    try:
        present = _run_probe(events_tables_present(conn_str, backend))
    except Exception:
        return "y"
    return "n" if present else "y"
