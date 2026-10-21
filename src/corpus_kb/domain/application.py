"""Eventsourcing application setup with a selectable store backend.

The backend picks the eventsourcing persistence module:
'postgres' -> eventsourcing.postgres (POSTGRES_* env vars);
'duckdb'   -> eventsourcing.sqlite   (SQLITE_DBNAME file beside the .duckdb).

The eventsourcing library owns event_store + snapshot_store tables.
Our custom projection tables (documents, chunks, vectors, etc.) are
updated by async projections that subscribe to events.

Usage:
    from corpus_kb.domain.application import get_app
    app = get_app()                          # postgres, DSN from config/env
    app = get_app(dsn, backend="duckdb")     # sqlite store on a file
    doc = Document(tenant_id=..., source='test.py', source_type='code')
    app.save(doc)  # persists events to the store
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any
from urllib.parse import urlparse

from eventsourcing.application import Application

from corpus_kb.errors import ConfigError

logger = logging.getLogger(__name__)

AppKey = tuple[str, str]


def _postgres_env(conn_str: str) -> dict[str, str]:
    """Parse a Postgres connection string into env vars for eventsourcing.

    Args:
        conn_str: e.g. postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb

    Returns:
        Dict of eventsourcing env vars (POSTGRES_HOST, POSTGRES_PORT, etc.)
    """
    parsed = urlparse(conn_str)
    return {
        "PERSISTENCE_MODULE": "eventsourcing.postgres",
        "POSTGRES_DBNAME": parsed.path.lstrip("/"),
        "POSTGRES_HOST": parsed.hostname or "localhost",
        "POSTGRES_PORT": str(parsed.port or 5432),
        "POSTGRES_USER": parsed.username or "corpus_user",
        "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_SCHEMA": "public",
        "CREATE_TABLE": "y",
    }


def _sqlite_path_from_conn_str(conn_str: str) -> str:
    """Map a duckdb DSN or bare path to a filesystem path for SQLITE_DBNAME.

    The DSN follows the program's duckdb convention: 'duckdb:///C:/x.duckdb'
    becomes the Windows path 'C:\\x.duckdb'. A bare path passes through,
    normalized to OS separators. ':memory:' (bare or in a DSN) stays the
    library's in-memory mode. A POSIX absolute file needs four slashes:
    'duckdb:////abs/x.duckdb'.
    """
    if "://" not in conn_str:
        return conn_str if conn_str == ":memory:" else os.path.normpath(conn_str)
    parsed = urlparse(conn_str)
    if parsed.scheme != "duckdb" or parsed.netloc:
        raise ConfigError(f"Connection string {conn_str!r} is not a duckdb:/// DSN or a file path.")
    path = parsed.path.removeprefix("/")
    return path if path == ":memory:" else os.path.normpath(path)


def _sqlite_env(conn_str: str) -> dict[str, str]:
    """Build the env for the eventsourcing.sqlite module."""
    return {
        "PERSISTENCE_MODULE": "eventsourcing.sqlite",
        "SQLITE_DBNAME": _sqlite_path_from_conn_str(conn_str),
        "CREATE_TABLE": "y",
    }


def _eventsourcing_env(backend: str, conn_str: str) -> dict[str, str]:
    """Build the eventsourcing env for one backend ('postgres' | 'duckdb')."""
    if backend == "postgres":
        return _postgres_env(conn_str)
    if backend == "duckdb":
        return _sqlite_env(conn_str)
    raise ConfigError(f"Unknown event-store backend {backend!r}. Use 'postgres' or 'duckdb'.")


class CorpusApplication(Application):
    """Eventsourcing application for Corpus-KB.

    Owns the event store and aggregate lifecycle.
    Projections subscribe to events and update read models.
    """

    # eventsourcing declares snapshotting_intervals as ClassVar and re-reads
    # it on every save. CorpusApplication binds the interval PER INSTANCE
    # (see __init__) so one construction can never rewrite another app's
    # snapshot behavior.
    snapshotting_intervals: dict[type[Any], int]

    def __init__(self, connection_string: str, backend: str = "postgres") -> None:
        # The parsed env is passed EXPLICITLY (the env param wins over
        # os.environ in construct_env) — mutating os.environ here would make
        # a later get_app(different_dsn) silently reuse this construction's
        # POSTGRES_* values and ignore its own argument.
        env = _eventsourcing_env(backend, connection_string)
        # Auto-snapshot the Document aggregate (todo-11 STEP 0, item (h)).
        # The interval is read from env AT CONSTRUCTION and bound to THIS
        # INSTANCE. snapshotting_intervals is a ClassVar in eventsourcing
        # and is re-read on every save, so the todo-11 shape (writing the
        # class attribute before super().__init__) let a SECOND app
        # construction — another DSN or backend, or one built while
        # CORPUS_KB_SNAPSHOT_PERIOD was unset — silently rewrite the FIRST
        # app's snapshot behavior. IS_SNAPSHOTTING_ENABLED rides in this
        # app's env instead of the class flag for the same reason.
        interval = int(os.environ.get("CORPUS_KB_SNAPSHOT_PERIOD", "100"))
        self.snapshotting_intervals = {}
        if interval > 0:
            from corpus_kb.domain.aggregates import Document

            self.snapshotting_intervals = {Document: interval}
            env["IS_SNAPSHOTTING_ENABLED"] = "y"
        super().__init__(env=env)


_apps: dict[AppKey, CorpusApplication] = {}
_apps_lock = threading.Lock()


def events_table_name(app: Application) -> str:
    """The recorder's backing events table.

    eventsourcing types the base recorder as ApplicationRecorder, which does
    not declare the name; every backend this app constructs (the
    eventsourcing-postgres recorder) carries ``events_table_name``. The
    getattr keeps that dependency optional at type level and raises a plain
    error when a recorder without the attribute is plugged in.
    """
    name = getattr(app.recorder, "events_table_name", None)
    if not name:
        raise RuntimeError("The configured event recorder has no events_table_name.")
    return str(name)


def _resolve_connection_string() -> str:
    """Find the connection string: env var first, then config.yaml."""
    connection_string = os.environ.get("CORPUS_KB_DATABASE_URL")
    if connection_string:
        return connection_string
    try:
        from corpus_kb.config import load_config

        cfg = load_config()
        db_cfg = cfg.get("database", {})
        return str(db_cfg.get("connection_string", ""))
    except Exception as e:
        logger.error("Failed to load database config: %s", e)
        raise RuntimeError(
            "No database connection string found. "
            "Set CORPUS_KB_DATABASE_URL or configure database.connection_string "
            "in config.yaml."
        ) from e


def dsn_from_config(cfg: dict[str, Any] | None) -> str | None:
    """The connection string inside a loaded config dict, or None.

    Runners use it to bind the event-store app to the SAME database their
    asyncpg pool reads (the app cache keys on (backend, DSN)). None (no
    config, or no database.connection_string in it) keeps the historical
    bare-get_app resolution: env var first, then config.yaml.
    """
    if not cfg:
        return None
    return str((cfg.get("database") or {}).get("connection_string", "") or "") or None


def get_app_for_config(cfg: dict[str, Any] | None) -> CorpusApplication:
    """The config-truth app resolution shared by every event-store caller.

    ``database.backend`` is the switch: the default keeps the bare
    ``get_app()`` resolution (env var first, then config.yaml);
    ``duckdb`` keys the app cache on the config DSN with the
    ``eventsourcing.sqlite`` store, which must NOT be the analytics
    ``.duckdb`` (SQLite cannot open it).
    """
    db_cfg = (cfg or {}).get("database", {})
    dsn = dsn_from_config(cfg)
    if str(db_cfg.get("backend") or "postgres") == "duckdb":
        return get_app(dsn or "", backend="duckdb")
    return get_app()


def get_app(connection_string: str | None = None, backend: str = "postgres") -> CorpusApplication:
    """Get or create the CorpusApplication cached under (backend, conn_str).

    Args:
        connection_string: backend DSN or file path. If None, loads from
            CORPUS_KB_DATABASE_URL or config.
        backend: 'postgres' (default) or 'duckdb' (sqlite event store).

    Returns:
        CorpusApplication for this cache key.
    """
    if connection_string is None:
        connection_string = _resolve_connection_string()
    if not connection_string:
        raise RuntimeError(
            "Database connection string is empty. "
            "Set CORPUS_KB_DATABASE_URL or configure database.connection_string."
        )
    key = (backend, connection_string)
    with _apps_lock:
        app = _apps.get(key)
        if app is None:
            logger.info("Initializing CorpusApplication with %s backend", backend)
            app = CorpusApplication(connection_string, backend=backend)
            _apps[key] = app
        return app


def close_app(key: AppKey) -> None:
    """Close the cached app for ``key`` and drop it (zero-drift rule 21).

    close() releases the store resources: the Postgres pool on the postgres
    backend, the sqlite connections on the duckdb backend. A later get_app
    with the same key builds a fresh app. An unknown key is a no-op.
    """
    with _apps_lock:
        app = _apps.pop(key, None)
    if app is not None:
        app.close()


def reset_app() -> None:
    """Close and drop every cached app (for testing)."""
    with _apps_lock:
        apps = list(_apps.values())
        _apps.clear()
    for app in apps:
        app.close()
