"""RAG backend protocol for Corpus-KB.

Defines the minimal async interface that any RAG orchestration layer must
implement. The initial implementation uses LlamaIndex + PGVectorStore + Ollama.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class RetrievalResult:
    """A single retrieved node from a RAG backend."""

    node_id: str
    source_id: str
    text: str
    score: float
    metadata: dict


@runtime_checkable
class RagBackend(Protocol):
    """Async protocol for RAG indexing and retrieval."""

    async def initialize(self) -> None:
        """Prepare the backend and verify connectivity."""
        ...

    async def ingest(self, source_id: str, chunks: list[dict]) -> None:
        """Index a list of chunks under the given source id."""
        ...

    async def retrieve(
        self,
        query: str,
        top_k: int,
        filters: dict | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve the top_k most relevant chunks for the query."""
        ...

    async def delete_source(self, source_id: str) -> None:
        """Remove all indexed chunks for the given source id."""
        ...

    async def health(self) -> bool:
        """Return True if the backend is reachable and operational."""
        ...
