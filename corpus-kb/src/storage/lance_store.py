"""LanceDB vector store for chunk embeddings.

Stores exactly four columns: chunk_id, vector, source_type, document_id.
Provenance columns live in SQLite, not here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, cast

import pyarrow as pa

from src.storage._lance_typed import LanceDBTable, connect
from src.utils.models import Chunk


class LanceDBStore:
    """Vectors-only LanceDB store for semantic retrieval."""

    def __init__(
        self,
        uri: str,
        dimensions: int,
        *,
        table_name: str = "chunks",
    ) -> None:
        """Initialize the vector store.

        Args:
            uri: LanceDB connection URI (directory path).
            dimensions: Expected vector dimensionality.
            table_name: Name of the table to use or create.
        """
        self._uri = uri
        self._dimensions = dimensions
        self._table_name = table_name
        self._schema = pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dimensions)),
                pa.field("source_type", pa.string()),
                pa.field("document_id", pa.string()),
            ]
        )
        Path(uri).mkdir(parents=True, exist_ok=True)
        self._db = connect(uri)
        self._table: Optional[LanceDBTable] = None
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Open an existing table or create it idempotently."""
        if self._table_name in self._db.list_tables().tables:
            self._table = self._db.open_table(self._table_name)
        else:
            self._table = self._db.create_table(
                self._table_name,
                schema=self._schema,
            )

    def add_chunks(self, chunks: list[Chunk]) -> None:
        """Add or update chunks in the vector store.

        Args:
            chunks: Non-empty list of Chunk objects with pre-computed embeddings.

        Raises:
            ValueError: If chunks is empty.
            RuntimeError: If the store has been closed.
        """
        if not chunks:
            raise ValueError("chunks must not be empty")
        if self._table is None:
            raise RuntimeError("store is closed")

        rows: list[dict[str, object]] = []
        for chunk in chunks:
            vector = chunk.embedding
            if vector is None:
                vector = [0.0] * self._dimensions
            rows.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "vector": vector,
                    "source_type": chunk.source_type,
                    "document_id": chunk.document_id,
                }
            )

        self._table.add(rows)

    def search(self, query_vector: list[float], k: int) -> list[Chunk]:
        """Find the k nearest vectors to the query vector.

        Args:
            query_vector: Embedding to search against.
            k: Maximum number of results to return.

        Returns:
            Up to k Chunk-shaped results with embeddings populated.

        Raises:
            RuntimeError: If the store has been closed.
        """
        if self._table is None:
            raise RuntimeError("store is closed")
        if k <= 0:
            return []

        results = self._table.search(query_vector).limit(k).to_arrow()
        chunk_ids = cast(list[str], results.column("chunk_id").to_pylist())
        vectors = cast(list[list[float]], results.column("vector").to_pylist())
        source_types = cast(list[str], results.column("source_type").to_pylist())
        document_ids = cast(list[str], results.column("document_id").to_pylist())

        out: list[Chunk] = []
        for chunk_id, vector, source_type, document_id in zip(
            chunk_ids, vectors, source_types, document_ids, strict=True
        ):
            out.append(
                Chunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    text="",
                    source_type=source_type,
                    embedding=vector,
                )
            )
        return out

    def close(self) -> None:
        """Release the table reference."""
        self._table = None
