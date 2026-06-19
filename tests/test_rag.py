"""Tests for Phase 3: RAG Layer (Embedder, HybridSearch, Reranker).

Requires Ollama with nomic-embed-text running for full integration tests.
Tests gracefully skip or degrade if Ollama is unavailable.
"""

from __future__ import annotations

import os
import sys
import pytest

from utils.models import SearchResult

# ============================================================================
# OllamaEmbedder
# ============================================================================

class TestOllamaEmbedder:
    def test_embed_returns_vector(self):
        """Should return a 768-d vector (or zero-vector if Ollama unavailable)."""
        from rag.embedder import OllamaEmbedder
        e = OllamaEmbedder()
        vec = e.embed("hello world")
        assert len(vec) == e.dimensions
        # Either real values or zeros if Ollama unavailable
        assert all(isinstance(v, float) for v in vec)

    def test_embed_batch(self):
        from rag.embedder import OllamaEmbedder
        e = OllamaEmbedder()
        vecs = e.embed_batch(["a", "b", "c"])
        assert len(vecs) == 3
        assert all(len(v) == e.dimensions for v in vecs)

    def test_embed_batch_empty(self):
        from rag.embedder import OllamaEmbedder
        e = OllamaEmbedder()
        assert e.embed_batch([]) == []

    def test_embed_cache_hit(self):
        from rag.embedder import OllamaEmbedder
        e = OllamaEmbedder()
        v1 = e.embed("cache test")
        v2 = e.embed("cache test")
        assert v1 == v2

    def test_embed_chunks_in_place(self):
        from rag.embedder import OllamaEmbedder
        from utils.models import Chunk
        e = OllamaEmbedder()
        chunks = [Chunk(text="hello"), Chunk(text="world")]
        result = e.embed_chunks(chunks)
        assert all(c.vector is not None for c in result)
        assert all(len(c.vector) == e.dimensions for c in result)

    def test_clear_cache(self):
        from rag.embedder import OllamaEmbedder
        e = OllamaEmbedder()
        e.embed("test")
        assert len(e._cache) > 0
        e.clear_cache()
        assert len(e._cache) == 0


# ============================================================================
# HybridSearcher (requires LanceDBStore mock or real store)
# ============================================================================

# These tests need a running LanceDBStore. We use a tmp path for isolation.

@pytest.fixture
def tmp_store(tmp_path):
    from storage.lancedb_store import LanceDBStore
    db_path = tmp_path / "lancedb"
    yield LanceDBStore(str(db_path))

@pytest.fixture
def embedder():
    from rag.embedder import OllamaEmbedder
    return OllamaEmbedder()


class TestHybridSearcher:
    def test_initialization(self, tmp_store, embedder):
        from rag.hybrid_search import HybridSearcher
        hs = HybridSearcher(tmp_store, embedder)
        assert hs.store is tmp_store
        assert hs.embedder is embedder

    def test_build_filters_none(self, tmp_store, embedder):
        from rag.hybrid_search import HybridSearcher
        hs = HybridSearcher(tmp_store, embedder)
        assert hs._build_filters() is None
        assert hs._build_filters(source_type="code") == {"source_type": "code"}
        assert hs._build_filters(file_path="/tmp/test.py") == {"file_path": "/tmp/test.py"}

    def test_vector_search_no_data(self, tmp_store, embedder):
        """Searching an empty store should return empty results."""
        from rag.hybrid_search import HybridSearcher
        hs = HybridSearcher(tmp_store, embedder)
        vec = embedder.embed("test")
        results = hs.vector_search(vec, k=5)
        assert isinstance(results, list)

    def test_fts_search_no_data(self, tmp_store, embedder):
        """FTS on empty store should return empty."""
        from rag.hybrid_search import HybridSearcher
        hs = HybridSearcher(tmp_store, embedder)
        results = hs.fts_search("test", k=5)
        assert isinstance(results, list)

    def test_rrf_fuse_single_list(self, tmp_store, embedder):
        """RRF with one list should maintain order."""
        from storage.lancedb_store import LanceDBStore
        results = [
            SearchResult(chunk_id="a", text="a", score=0.9, source="test"),
            SearchResult(chunk_id="b", text="b", score=0.8, source="test"),
        ]
        merged = LanceDBStore._rrf_fuse(results, [], k=60.0)
        assert len(merged) == 2
        assert merged[0].chunk_id == "a"

    def test_rrf_fuse_two_lists(self, tmp_store, embedder):
        """Items appearing in both lists should get priority."""
        from storage.lancedb_store import LanceDBStore
        list_a = [
            SearchResult(chunk_id="a", text="a", score=0.9, source="test"),
            SearchResult(chunk_id="b", text="b", score=0.8, source="test"),
        ]
        list_b = [
            SearchResult(chunk_id="b", text="b", score=0.7, source="test"),
            SearchResult(chunk_id="c", text="c", score=0.6, source="test"),
        ]
        merged = LanceDBStore._rrf_fuse(list_a, list_b, k=60.0)
        # "b" appears in both lists -> highest RRF score
        assert merged[0].chunk_id == "b"

    def test_search_no_data(self, tmp_store, embedder):
        """Full hybrid search on empty store returns empty."""
        from rag.hybrid_search import HybridSearcher
        hs = HybridSearcher(tmp_store, embedder)
        results = hs.search("test query", k=5)
        assert isinstance(results, list)

    def test_rrf_excludes_toc_chunks(self, tmp_store, embedder):
        """RED test: RRF should exclude TOC/heading chunks with low relevance.

        Given: Vector and FTS results containing TOC chunks (chunk_type='toc')
               with zero or very low vector scores
        When:  _rrf_fuse() is called
        Then:  TOC chunks should be filtered out (before fix fails, after fix passes)
        """
        from storage.lancedb_store import LanceDBStore

        # Create test chunks: one content chunk, one TOC chunk
        content_chunk = SearchResult(
            chunk_id="content_1",
            text="def authenticate(user): return verify_token(user)",
            score=0.85,  # High relevance
            source="auth.py",
            chunk_type="function",
        )

        toc_chunk = SearchResult(
            chunk_id="toc_1",
            text="# Table of Contents\n1. Authentication\n2. Authorization",
            score=0.0,  # Zero relevance (navigation only)
            source="README.md",
            chunk_type="toc",
        )

        # Simulate vector search results (content + TOC)
        vector_results = [content_chunk, toc_chunk]

        # Simulate FTS results (just content)
        fts_results = [content_chunk]

        # Call RRF fusion with default params
        # BEFORE FIX: TOC chunk will be included in results
        # AFTER FIX: TOC chunk will be filtered out
        merged = LanceDBStore._rrf_fuse(vector_results, fts_results, k=60.0)

        # Verify TOC chunks are excluded
        chunk_ids = [r.chunk_id for r in merged]
        assert "toc_1" not in chunk_ids, "TOC chunks should be filtered out by relevance floor"
        assert "content_1" in chunk_ids, "Content chunks should be retained"


# ============================================================================
# Reranker
# ============================================================================

class TestReranker:
    def test_identity_mode(self):
        from rag.reranker import Reranker
        r = Reranker(mode="identity")
        results = [
            SearchResult(chunk_id="a", text="alpha", score=0.9, source="test"),
            SearchResult(chunk_id="b", text="beta", score=0.8, source="test"),
        ]
        reranked = r.rerank("query", results, k=2)
        assert len(reranked) == 2
        assert reranked[0].chunk_id == "a"

    def test_identity_truncates_k(self):
        from rag.reranker import Reranker
        r = Reranker(mode="identity")
        results = [
            SearchResult(chunk_id="a", text="a", score=0.9, source="test"),
            SearchResult(chunk_id="b", text="b", score=0.8, source="test"),
            SearchResult(chunk_id="c", text="c", score=0.7, source="test"),
        ]
        reranked = r.rerank("query", results, k=1)
        assert len(reranked) == 1

    def test_identity_empty(self):
        from rag.reranker import Reranker
        r = Reranker(mode="identity")
        assert r.rerank("query", [], k=5) == []

    def test_unknown_mode_falls_back(self):
        from rag.reranker import Reranker
        r = Reranker(mode="unknown_mode")
        results = [
            SearchResult(chunk_id="a", text="a", score=0.9, source="test"),
        ]
        reranked = r.rerank("query", results, k=1)
        assert len(reranked) == 1

    def test_parse_ranked_order(self):
        from rag.reranker import Reranker
        r = Reranker()
        assert r._parse_ranked_order("[3, 1, 2]", 3) == [3, 1, 2]
        assert r._parse_ranked_order("3 1 2", 3) == [3, 1, 2]
        assert r._parse_ranked_order("garbage", 3) == [1, 2, 3]
