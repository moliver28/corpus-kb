"""Storage backends for Corpus-KB.

Exports the public storage classes used by the ingest and search pipelines.
"""

from __future__ import annotations

from .graph_store import GraphStore, PostgresGraphStore

__all__ = ["GraphStore", "PostgresGraphStore"]