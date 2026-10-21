"""Gateway-aware store bootstrap for the CLI legs (storage portability).

One bootstrap for the release/report/view CLI commands: the config DSN
resolves to an ``asyncpg`` pool (Postgres — byte-identical to the legacy
``wired_pool`` path) or a :class:`~corpus_kb.storage.gateway.DuckDbGateway`
(``duckdb:///`` DSN, same dispatch the migrate runner uses). The CLI layer
stays storage-blind: commands hand ``wired.store`` to the shared handlers
and render the payload.

``wired_pool`` (``cli_support.py``) keeps serving the research-cycle
commands unchanged; the legs that are ported move here. The research
embedder rides the Postgres pool path only — the DuckDB embedder port
lives with the research-store work package.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, NamedTuple, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.storage.gateway import SqlGateway

#: The DuckDB DSN scheme (``_setup.migrate.DUCKDB_DSN_PREFIX``). A DSN with
#: this prefix names a DuckDB database file; anything else opens as Postgres.
DUCKDB_DSN_PREFIX = "duckdb:///"


class StoreWired(NamedTuple):
    """What one ported CLI coroutine gets from the bootstrap."""

    store: asyncpg.Pool | SqlGateway
    cfg: dict[str, object]
    conn_str: str
    embedder: ResearchEmbedder | None


@asynccontextmanager
async def wired_store(
    *, embedder: bool = False, cfg: dict[str, object] | None = None
) -> AsyncIterator[StoreWired]:
    """Open the configured store (pool or gateway); close it on exit.

    ``embedder=True`` builds a ``ResearchEmbedder`` on the Postgres path
    (the ask/uncoded wiring); on DuckDB it stays None — the embedder is a
    Postgres-typed component today.

    Bootstrap failures go through the taxonomy (zero-drift rule 4): an
    unparseable DSN raises ConfigError, an unreachable database raises
    DatabaseUnavailableError, and the gateway's typed lock error passes
    through unchanged.
    """
    import asyncpg

    from corpus_kb.config import load_config
    from corpus_kb.errors import ConfigError, CorpusError, DatabaseUnavailableError

    if cfg is None:
        cfg = load_config()
    db = cast(dict[str, object], cfg.get("database") or {})
    conn_str = str(db.get("connection_string", ""))
    research_embedder: ResearchEmbedder | None = None
    if conn_str.startswith(DUCKDB_DSN_PREFIX):
        from corpus_kb.storage.gateway import DuckDbGateway

        if len(conn_str) <= len(DUCKDB_DSN_PREFIX):
            raise ConfigError(
                "The DuckDB DSN names no file. Use the form duckdb:///C:/data/x.duckdb."
            )
        try:
            store: asyncpg.Pool | SqlGateway = await DuckDbGateway.connect(conn_str)
        except ValueError as exc:
            raise ConfigError(
                f"Connection string {conn_str!r} is not a usable duckdb:/// DSN."
                " Use the form duckdb:///C:/data/x.duckdb."
            ) from exc
        except CorpusError:
            raise  # the gateway already typed lock (and other) failures
        except Exception as exc:
            raise DatabaseUnavailableError(
                "The DuckDB database file did not open.",
                remediation="Check the file path in database.connection_string. Then try again.",
            ) from exc
    else:
        if not conn_str:
            raise ConfigError(
                "No database connection string is set."
                " Set CORPUS_KB_DATABASE_URL or database.connection_string."
            )
        try:
            pool = await asyncpg.create_pool(conn_str)
        except ValueError as exc:  # asyncpg's DSN parse failures are ValueErrors
            raise ConfigError(
                f"Connection string {conn_str!r} is not a usable postgresql:// DSN."
            ) from exc
        except Exception as exc:
            raise DatabaseUnavailableError(
                "The Postgres database did not accept a connection."
            ) from exc
        store = pool
        if embedder:
            from corpus_kb.projections.research._embed import ResearchEmbedder
            from corpus_kb.rag import create_embedder

            research_embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
    try:
        yield StoreWired(store=store, cfg=cfg, conn_str=conn_str, embedder=research_embedder)
    finally:
        await store.close()
