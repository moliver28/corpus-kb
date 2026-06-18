"""MCP tools for searching the RAG knowledge base.

Tools:
- search: Hybrid search over all chunks
- search_context: Search with parent/child/sibling context expansion
"""

from __future__ import annotations

from typing import Optional

from storage.lancedb_store import LanceDBStore
from rag.embedder import OllamaEmbedder
from rag.hybrid_search import HybridSearcher
from rag.reranker import Reranker
from utils.models import SearchResult


def register_tools(
    mcp,
    store: LanceDBStore,
    embedder: OllamaEmbedder,
):
    """Register all search tools with the MCP server."""
    searcher = HybridSearcher(store, embedder)
    reranker = Reranker(mode="identity")

    @mcp.tool()
    def search(
        query: str,
        k: int = 10,
        source_type: Optional[str] = None,
    ) -> list[dict]:
        """Hybrid search (vector + full-text + RRF) across all chunks.

        Args:
            query: Natural language query.
            k: Number of results (max 50).
            source_type: Optional filter: "code", "markdown", or "text".

        Returns:
            List of search results with text, source, score, and metadata.
        """
        k = min(k, 50)
        results = searcher.search(query, k=k, source_type=source_type)
        results = reranker.rerank(query, results, k=k)
        return [_result_to_dict(r) for r in results]

    @mcp.tool()
    def search_context(
        query: str,
        k: int = 5,
        context_chunks: int = 2,
        source_type: Optional[str] = None,
    ) -> list[dict]:
        """Search with parent/sibling/child context expansion.

        For each result, includes surrounding chunks (context_chunks
        before and after) so the LLM has full context.

        Args:
            query: Natural language query.
            k: Number of primary results (max 20).
            context_chunks: Number of adjacent chunks to include (0-5).
            source_type: Optional filter: "code", "markdown", or "text".

        Returns:
            List of results, each with a "context" field containing
            surrounding chunks.
        """
        k = min(k, 20)
        context_chunks = max(0, min(context_chunks, 5))

        results = searcher.search(query, k=k, source_type=source_type)

        expanded = []
        for r in results:
            result_dict = _result_to_dict(r)
            # Fetch context around this chunk
            siblings = store.get_chunk_context(
                r.chunk_id,
                before=context_chunks,
                after=context_chunks,
            )
            result_dict["context"] = [_result_to_dict(s) for s in siblings]
            expanded.append(result_dict)

        return expanded


def _result_to_dict(r: SearchResult) -> dict:
    """Convert SearchResult to a serializable dict."""
    return {
        "chunk_id": r.chunk_id,
        "text": r.text,
        "score": round(r.score, 4),
        "source": r.source,
        "doc_id": r.doc_id,
        "chunk_type": r.chunk_type,
        "entity_name": r.entity_name,
        "heading_path": r.heading_path,
        "scope_chain": r.scope_chain,
        "file_path": r.file_path,
        "start_line": r.start_line,
        "end_line": r.end_line,
        "context_type": r.context_type,
    }
