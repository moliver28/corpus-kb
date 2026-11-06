"""The one store opener for both engines (issue #330 Part 1).

Every surface that used to call ``asyncpg.create_pool`` directly opens its
store HERE instead: a ``duckdb:///`` DSN builds a
:class:`~corpus_kb.storage.gateway.DuckDbGateway`, anything else builds an
asyncpg pool. Both expose ``close()``, so callers close one shape. The
typed errors stay typed: a bad DSN raises ``ConfigError``, an unreachable
store raises ``DatabaseUnavailableError``, and the gateway's file-lock
error passes through unchanged (its remediation already names the fix).

:func:`open_pool_typed_store` serves call sites whose signatures say
``asyncpg.Pool``: the runtime object may be a gateway and the tools
dispatch the engine with ``storage.gateway.is_gateway``. The declared type
keeps those signatures unchanged during the port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from corpus_kb.errors import ConfigError, DatabaseUnavailableError

if TYPE_CHECKING:
    import asyncpg

    from corpus_kb.storage.gateway import DuckDbGateway, SqlGateway

#: The DuckDB DSN scheme (``_setup.migrate.DUCKDB_DSN_PREFIX``). A DSN with
#: this prefix names a DuckDB database file; anything else opens as Postgres.
DUCKDB_DSN_PREFIX = "duckdb:///"


def is_duckdb_dsn(conn_str: str) -> bool:
    """True when the connection string names a DuckDB file."""
    return conn_str.startswith(DUCKDB_DSN_PREFIX)


def config_conn_str(cfg: dict[str, Any]) -> str:
    """The config DSN, ``duckdb:///``-prefixed when the backend is duckdb.

    ``database.backend: duckdb`` with a bare file path is the supported
    shape (the env override table documents the key), so the prefix is
    added HERE instead of demanding every config file spell the scheme.
    A DSN that already carries the scheme passes through unchanged;
    Postgres configs pass through unchanged.
    """
    db = cast("dict[str, Any]", cfg.get("database") or {})
    conn_str = str(db.get("connection_string") or "")
    backend = str(db.get("backend") or "postgres")
    if backend == "duckdb" and conn_str and not is_duckdb_dsn(conn_str):
        return DUCKDB_DSN_PREFIX + conn_str.replace("\\", "/")
    return conn_str


async def open_store(conn_str: str) -> asyncpg.Pool | DuckDbGateway:
    """Open the store one connection string names; caller closes it.

    Postgres arm: the shared pool settings (min 5, max 20, 60s command
    timeout). DuckDB arm: the gateway connect, whose lock error stays the
    typed ``DatabaseUnavailableError`` with the close-the-file remediation.
    """
    if is_duckdb_dsn(conn_str):
        from corpus_kb.storage.gateway import DuckDbGateway

        if len(conn_str) <= len(DUCKDB_DSN_PREFIX):
            raise ConfigError(
                "The DuckDB DSN names no file. Use the form duckdb:///C:/data/x.duckdb."
            )
        return await DuckDbGateway.connect(conn_str)
    import asyncpg

    if not conn_str:
        raise ConfigError(
            "No database connection string. Set CORPUS_KB_DATABASE_URL or "
            "configure database.connection_string in config.yaml."
        )
    try:
        return await asyncpg.create_pool(
            conn_str,
            min_size=5,
            max_size=20,
            command_timeout=60.0,
        )
    except Exception as exc:
        raise DatabaseUnavailableError(
            f"Failed to connect to Postgres at {conn_str}: {exc}",
            remediation="Run corpus-kb setup. Check the DSN and credentials.",
        ) from exc


async def open_pool_typed_store(conn_str: str) -> asyncpg.Pool:
    """:func:`open_store`, typed for ``asyncpg.Pool`` tool signatures.

    The runtime object is the pool on Postgres or a gateway on DuckDB; the
    tool layer dispatches with ``is_gateway`` (the long-standing duck-typed
    seam). Only the declared type is widened here.
    """
    return cast("asyncpg.Pool", await open_store(conn_str))


def pg_pool_of(store: asyncpg.Pool | SqlGateway | None) -> asyncpg.Pool | None:
    """The asyncpg pool, or None when the store is a gateway.

    The pgml embedder and the rerankers need a pool for their SQL; on
    DuckDB they are not wired (the config mix gate refuses pgml there), so
    None is the honest answer on the gateway.
    """
    from corpus_kb.storage.gateway import is_gateway

    if store is None or is_gateway(store):
        return None
    return cast("asyncpg.Pool", store)


def resolve_event_backend(dsn: str, backend: str | None) -> str:
    """The event-store backend: the explicit choice, else the DSN scheme.

    ``domain.application.get_app`` passes ``backend=None`` so an explicit
    ``duckdb:///`` DSN selects the SQLite sidecar WITHOUT a config read —
    one fact (the scheme) decides, and a Postgres DSN stays Postgres.
    """
    if backend is not None:
        return backend
    return "duckdb" if is_duckdb_dsn(dsn) else "postgres"
