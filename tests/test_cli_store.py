"""Typed-bootstrap tests for the CLI store seam (storage portability).

Hermetic: the failure taxonomy of ``wired_store`` (zero-drift rule 4) and
one happy-path open per backend. No server, no Ollama; the locked-file
case holds a real DuckDB handle to force the gateway's typed lock error.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

duckdb = pytest.importorskip("duckdb")

from corpus_kb.errors import ConfigError, DatabaseUnavailableError  # noqa: E402


def _cfg(conn_str: str) -> dict[str, Any]:
    return {"database": {"connection_string": conn_str}}


async def test_wired_store_opens_the_duckdb_gateway_and_closes_it(tmp_path) -> None:
    from corpus_kb.cli.cli_store import wired_store
    from corpus_kb.storage.duckdb_gateway import DuckDbGateway

    conn_str = f"duckdb:///{(tmp_path / 'wired.duckdb').as_posix()}"
    async with wired_store(cfg=_cfg(conn_str)) as wired:
        assert isinstance(wired.store, DuckDbGateway)
        assert wired.embedder is None  # the embedder is a Postgres component
        assert await wired.store.fetchval("SELECT 40 + 2") == 42


async def test_wired_store_refuses_a_fileless_duckdb_dsn() -> None:
    from corpus_kb.cli.cli_store import wired_store

    with pytest.raises(ConfigError, match="names no file"):
        async with wired_store(cfg=_cfg("duckdb:///")):
            pass


async def test_wired_store_maps_a_duckdb_open_failure_to_the_taxonomy(tmp_path) -> None:
    """A non-lock open failure (garbage file) raises DatabaseUnavailableError."""
    from corpus_kb.cli.cli_store import wired_store

    junk = tmp_path / "junk.duckdb"
    junk.write_bytes(b"definitely not a duckdb file" * 10)
    with pytest.raises(DatabaseUnavailableError, match="did not open"):
        async with wired_store(cfg=_cfg(f"duckdb:///{junk.as_posix()}")):
            pass


async def test_wired_store_keeps_the_typed_lock_error(tmp_path) -> None:
    """A held file handle trips the gateway's typed lock error unchanged."""
    from corpus_kb.cli.cli_store import wired_store

    path = str(tmp_path / "locked.duckdb")
    holder = duckdb.connect(path)
    try:
        with pytest.raises(DatabaseUnavailableError):
            async with wired_store(cfg=_cfg(f"duckdb:///{path}")):
                pass
    finally:
        holder.close()


async def test_wired_store_refuses_an_empty_postgres_dsn() -> None:
    from corpus_kb.cli.cli_store import wired_store

    with pytest.raises(ConfigError, match="No database connection string"):
        async with wired_store(cfg=_cfg("")):
            pass


async def test_wired_store_maps_a_malformed_postgres_dsn_to_config_error() -> None:
    from corpus_kb.cli.cli_store import wired_store

    with pytest.raises(ConfigError, match="not a usable postgresql:// DSN"):
        async with wired_store(cfg=_cfg("not a dsn at all")):
            pass


async def test_wired_store_maps_a_dead_postgres_to_database_unavailable() -> None:
    from corpus_kb.cli.cli_store import wired_store

    dead = f"postgresql://corpus_user:nope@127.0.0.1:1/{uuid4().hex}"
    with pytest.raises(DatabaseUnavailableError, match="did not accept a connection"):
        async with wired_store(cfg=_cfg(dead)):
            pass
