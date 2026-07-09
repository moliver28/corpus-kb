"""Eventsourcing application setup with Postgres backend.

The eventsourcing library owns event_store + snapshot_store tables.
Our custom projection tables (documents, chunks, vectors, etc.) are
updated by async projections that subscribe to events.

Usage:
    from domain.application import get_app
    app = get_app()
    doc = Document(tenant_id=..., source='test.py', source_type='code')
    app.save(doc)  # persists events to event_store
"""

from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import urlparse

from eventsourcing.application import Application

logger = logging.getLogger(__name__)


def _parse_connection_string(conn_str: str) -> dict[str, str]:
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


class CorpusApplication(Application):
    """Eventsourcing application for Corpus-KB.

    Owns the event store and aggregate lifecycle.
    Projections subscribe to events and update read models.
    """

    def __init__(self, connection_string: str) -> None:
        env = _parse_connection_string(connection_string)
        # Merge with os.environ (os.environ takes precedence for overrides)
        for key, value in env.items():
            if key not in os.environ:
                os.environ[key] = value
        super().__init__()


_app: Optional[CorpusApplication] = None


def get_app(connection_string: Optional[str] = None) -> CorpusApplication:
    """Get or create the singleton CorpusApplication instance.

    Args:
        connection_string: Postgres connection string. If None, loads from
            config or CORPUS_KB_DATABASE_URL env var.

    Returns:
        CorpusApplication singleton
    """
    global _app
    if _app is not None:
        return _app

    if connection_string is None:
        # Try env var first
        connection_string = os.environ.get("CORPUS_KB_DATABASE_URL")
        if not connection_string:
            # Fall back to config
            try:
                from config import load_config

                cfg = load_config()
                db_cfg = cfg.get("database", {})
                connection_string = db_cfg.get("connection_string", "")
            except Exception as e:
                logger.error("Failed to load database config: %s", e)
                raise RuntimeError(
                    "No database connection string found. "
                    "Set CORPUS_KB_DATABASE_URL or configure database.connection_string "
                    "in config.yaml."
                ) from e

    if not connection_string:
        raise RuntimeError(
            "Database connection string is empty. "
            "Set CORPUS_KB_DATABASE_URL or configure database.connection_string."
        )

    logger.info("Initializing CorpusApplication with Postgres backend")
    _app = CorpusApplication(connection_string)
    logger.info("CorpusApplication initialized successfully")
    return _app


def reset_app() -> None:
    """Reset the singleton (for testing)."""
    global _app
    _app = None