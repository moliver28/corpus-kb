"""Storage backends for Corpus-KB.

Exports the public storage classes used by the ingest and search pipelines.
"""

from __future__ import annotations

from .age_graph_store import AgeGraphStore, AgeUnavailableError
from .graph_store import GraphStore, PostgresGraphStore

# Safe for base installs: the backend imports llama-index only when
# CONSTRUCTED (the [llamaindex] extra), never at module import.
from .llamaindex_backend import LlamaIndexPostgresBackend
from .rag_backend import RagBackend, RetrievalResult

__all__ = [
    "AgeGraphStore",
    "AgeUnavailableError",
    "GraphStore",
    "LlamaIndexPostgresBackend",
    "PostgresGraphStore",
    "RagBackend",
    "RetrievalResult",
]
