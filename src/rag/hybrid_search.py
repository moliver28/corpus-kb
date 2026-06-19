"""Hybrid search — vector + full-text + Reciprocal Rank Fusion.

Orchestrates multi-modal search across LanceDBStore and merges results
using RRF (Reciprocal Rank Fusion). Supports configurable weights for
vector vs. FTS contribution.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from storage.lancedb_store import LanceDBStore
from utils.models import SearchResult

if TYPE_CHECKING:
    from .embedder import OllamaEmbedder


class HybridSearcher:
    """Combines vector similarity and full-text search with RRF merging.

    Typical flow:
        1. User submits a query string
        2. HybridSearcher embeds the query (using the embedder)
        3. Runs vector search + FTS search in parallel
        4. Merges results using Reciprocal Rank Fusion
    """

    def __init__(
        self,
        store: LanceDBStore,
        embedder: "OllamaEmbedder",  # noqa: F821
        k_vector: int = 20,
        k_fts: int = 20,
        rrf_k: int = 60,
    ):
        self.store = store
        self.embedder = embedder
        self.k_vector = k_vector
        self.k_fts = k_fts
        self.rrf_k = rrf_k

    def search(
        self,
        query: str,
        k: int = 10,
        source_type: Optional[str] = None,
        file_path: Optional[str] = None,
        relevance_floor: float = 0.3,
        excluded_chunk_types: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        """Run hybrid search with RRF fusion.

        Args:
            query: Natural language query string.
            k: Number of top results to return.
            source_type: Optional filter ("code", "markdown", "text").
            file_path: Optional file path filter.
            relevance_floor: Minimum vector score to include (default 0.3).
            excluded_chunk_types: Chunk types to exclude (default ["heading", "toc", "inventory"]).

        Returns:
            List of SearchResult objects sorted by RRF score (descending).
        """
        # 1. Get query embedding
        query_vector = self.embedder.embed(query)

        # 2. Run searches in parallel
        vector_results = self.vector_search(query_vector, k=self.k_vector,
                                            source_type=source_type,
                                            file_path=file_path)
        fts_results = self.fts_search(query, k=self.k_fts,
                                      source_type=source_type,
                                      file_path=file_path)

        # 3. Merge via RRF using LanceDBStore's implementation
        merged = self.store._rrf_fuse(vector_results, fts_results, k=self.rrf_k,
                                      relevance_floor=relevance_floor,
                                      excluded_chunk_types=excluded_chunk_types)

        # 4. Truncate to requested count
        return merged[:k]

    def vector_search(
        self,
        query_vector: list[float],
        k: int = 20,
        source_type: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> list[SearchResult]:
        """Run pure vector similarity search."""
        filters = self._build_filters(source_type, file_path)
        return self.store.search_vector(query_vector, k=k, filters=filters)

    def fts_search(
        self,
        query: str,
        k: int = 20,
        source_type: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> list[SearchResult]:
        """Run pure full-text search."""
        filters = self._build_filters(source_type, file_path)
        return self.store.search_fts(query, k=k, filters=filters)

    def _build_filters(
        self,
        source_type: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> Optional[dict]:
        """Build filter dict for LanceDB search."""
        filters: dict[str, str] = {}

        if source_type:
            filters["source_type"] = source_type

        if file_path:
            filters["file_path"] = file_path

        return filters if filters else None


