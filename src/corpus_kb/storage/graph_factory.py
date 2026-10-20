"""Graph store factory — the one selection point for graph backends.

Moved out of ``server_wiring.py`` (storage portability program §3.4): the
server wiring stays a thin composer and the factory lives beside the stores
it selects. Graph stores stay OFF the SqlGateway (§3.1 — session-GUC
semantics), so the factory's source argument is backend-specific: the
asyncpg pool on Postgres, one DuckDB connection on DuckDB.

Backends:

  - ``database.backend: postgres`` (default): ``graph.backend`` picks
    ``AgeGraphStore`` (probed, with a PostgresGraphStore fallback when
    Apache AGE is absent) or ``PostgresGraphStore`` directly.
  - ``database.backend: duckdb``: ``DuckDbGraphStore`` over the file named
    by ``database.connection_string``. Postgres-only engine selections in
    the config are refused first — the Wave A pure function, with the
    banked remediation (never a silent downgrade).

The caller owns closing: ``DuckDbGraphStore.close()`` never closes a
connection it did not open (Windows rule 13 — close before delete).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import asyncpg

from corpus_kb.config import AGE_ON_DUCKDB_ERROR, backend_mix_errors
from corpus_kb.errors import ConfigError
from corpus_kb.storage.duckdb_store import DuckDbGraphStore
from corpus_kb.storage.duckdb_support import duckdb_conn_string_to_path, map_connect_error
from corpus_kb.storage.graph_store import GraphStore

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

logger = logging.getLogger(__name__)

__all__ = ["create_graph_store", "open_duckdb_connection"]


async def open_duckdb_connection(conn_string: str) -> DuckDBPyConnection:
    """Open the DuckDB file named by a ``duckdb:///`` connection string.

    Same connect posture as ``DuckDbGateway.connect``: the scheme is
    stripped before duckdb sees it (duckdb would treat a URL as a file
    name), PG null ordering is pinned per connection, and connect
    failures map onto the shared taxonomy (the file-lock remediation).
    """
    import duckdb

    try:
        conn = duckdb.connect(
            duckdb_conn_string_to_path(conn_string),
            config={"default_null_order": "postgres"},
        )
    except Exception as exc:
        raise map_connect_error(exc) from exc
    conn.execute("SET enable_progress_bar = false")
    return conn


async def _duckdb_graph_store(
    cfg: dict[str, Any],
    source: asyncpg.Pool | DuckDBPyConnection | None,
) -> DuckDbGraphStore:
    """Select the DuckDB graph store; open the configured file when ungiven."""
    if source is not None:
        return DuckDbGraphStore(cast("DuckDBPyConnection", source))
    database = cfg.get("database", {}) or {}
    conn_string = str(database.get("connection_string", ""))
    if not conn_string:
        raise ConfigError(
            "No database connection string for the DuckDB graph store.",
            remediation="Set database.connection_string to a duckdb:/// file path.",
        )
    return DuckDbGraphStore(await open_duckdb_connection(conn_string))


async def create_graph_store(
    cfg: dict[str, Any],
    source: asyncpg.Pool | DuckDBPyConnection | None = None,
    backend: str | None = None,
) -> GraphStore:
    """Instantiate the configured GraphStore backend.

    ``backend`` overrides ``database.backend`` when given (the server
    wiring reads the key once and passes it down). On Postgres the
    ``graph.backend`` selection applies (age with fallback, or postgres);
    on DuckDB the recursive-CTE store is the only graph engine.
    """
    from corpus_kb.storage import AgeGraphStore, AgeUnavailableError, PostgresGraphStore

    database = cfg.get("database", {}) or {}
    selected = str(backend or database.get("backend") or "postgres")

    # Postgres-only engines are refused on duckdb — the Wave A mix gate,
    # re-checked here so hand-built config dicts cannot route around it.
    mix_errors = backend_mix_errors(cfg)
    if mix_errors:
        raise ConfigError(mix_errors[0])

    if selected == "duckdb":
        graph_cfg = cfg.get("graph", {}) or {}
        if graph_cfg.get("backend") == "age":
            raise ConfigError(AGE_ON_DUCKDB_ERROR)
        return await _duckdb_graph_store(cfg, source)

    if selected == "postgres":
        graph_backend = (cfg.get("graph", {}) or {}).get("backend", "age")
        pool = cast("asyncpg.Pool", source)
        if graph_backend == "age":
            try:
                store: GraphStore = AgeGraphStore(pool)
                await store.search_entities("")  # probe AGE availability
                return store
            except AgeUnavailableError as exc:
                logger.warning(
                    "Apache AGE graph backend unavailable: %s. Falling back to PostgresGraphStore.",
                    exc,
                )
                return PostgresGraphStore(pool)
        if graph_backend == "postgres":
            return PostgresGraphStore(pool)
        raise ValueError(f"Unknown graph backend: {graph_backend!r}. Use 'age' or 'postgres'.")
    raise ValueError(f"Unknown database backend: {selected!r}. Use 'postgres' or 'duckdb'.")
