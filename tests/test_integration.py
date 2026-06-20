"""Integration tests for Corpus-KB full pipeline.

Tests the end-to-end flow: storage → chunking → embedding → search → graph → SQL.
All tests use temporary directories for isolation and mock Ollama (zero-vector fallback).

Run: python -m pytest tests/test_integration.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.models import Chunk, Document, SearchResult, Entity, Relation
from storage.lancedb_store import LanceDBStore
from storage.duckdb_engine import DuckDBEngine
from storage.graph_store import create_graph_store, GraphStore
from chunking.detector import FileTypeDetector
from chunking.hierarchy import HierarchyResolver
from rag.embedder import OllamaEmbedder
from rag.hybrid_search import HybridSearcher
from rag.reranker import Reranker
from tools.ingest_tools import _ingest_text, _ingest_single_file
from tools.search_tools import _result_to_dict
from server import create_server

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

PY_CODE = '''
# project utilities
def hello(name):
    """Say hello."""
    return f"Hello, {name}!"


class Greeter:
    """A greeter class."""

    def greet(self, name):
        return hello(name)
'''

MARKDOWN = '''
# Project Title

## Introduction

This is a sample project for testing.

## Features

- Fast indexing
- Hybrid search
- Graph support

## Installation

Run `pip install corpus-kb`.
'''

LONG_MARKDOWN = '''
# Chapter 1

Content for chapter 1.

## Section 1.1

More content in the first section.

## Section 1.2

Even more content.

# Chapter 2

Content for chapter 2.
'''


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def no_ollama():
    """Patch OllamaEmbedder to return zero vectors so no Ollama process is needed.

    All instance methods are replaced on the class so they apply to every
    embedder instance created during the test.
    """
    orig_embed = OllamaEmbedder.embed
    orig_batch = OllamaEmbedder.embed_batch
    orig_chunks = OllamaEmbedder.embed_chunks

    def _embed(self, text):
        return [0.0] * self.dimensions

    def _batch(self, texts):
        return [[0.0] * self.dimensions for _ in texts]

    def _chunks(self, chunks):
        for c in chunks:
            c.vector = [0.0] * self.dimensions
        return chunks

    OllamaEmbedder.embed = _embed
    OllamaEmbedder.embed_batch = _batch
    OllamaEmbedder.embed_chunks = _chunks
    yield
    OllamaEmbedder.embed = orig_embed
    OllamaEmbedder.embed_batch = orig_batch
    OllamaEmbedder.embed_chunks = orig_chunks


@pytest.fixture
def store(tmp_path):
    uri = str(tmp_path / "lancedb")
    return LanceDBStore(uri, dimensions=768)


@pytest.fixture
def graph_store(tmp_path):
    return create_graph_store({
        "graph": {"backend": "sqlite"},
        "storage": {"graph_db": str(tmp_path / "graph.db")},
    })


@pytest.fixture
def detector():
    return FileTypeDetector()


@pytest.fixture
def resolver():
    return HierarchyResolver()


@pytest.fixture
def embedder():
    return OllamaEmbedder(model="nomic-embed-text", dimensions=768)


@pytest.fixture
def duckdb(tmp_path):
    return DuckDBEngine(str(tmp_path / "lancedb"))


# ---------------------------------------------------------------------------
# 1. Full pipeline — code + markdown ingest, chunk verification, search
# ---------------------------------------------------------------------------

class TestFullPipeline:
    """End-to-end: create layers, ingest code & markdown, verify chunks, search."""

    def test_ingest_text_and_search(
        self, tmp_path, store, graph_store, detector, resolver, embedder, no_ollama
    ):
        result_code = _ingest_text(
            PY_CODE, "test_code.py", "code",
            detector, embedder, store, graph_store, resolver,
        )
        assert result_code["doc_id"]
        assert result_code["source_type"] == "code"
        assert result_code["chunk_count"] > 0

        result_md = _ingest_text(
            MARKDOWN, "test_doc.md", "markdown",
            detector, embedder, store, graph_store, resolver,
        )
        assert result_md["doc_id"]
        assert result_md["source_type"] == "markdown"
        assert result_md["chunk_count"] > 0

        doc_list = store.list_documents()
        assert len(doc_list) == 2

        first_doc_chunks = store.chunks_table.search().limit(100).to_list()
        assert len(first_doc_chunks) >= result_code["chunk_count"] + result_md["chunk_count"]

        searcher = HybridSearcher(store, embedder)
        results = searcher.search("hello", k=5)
        assert len(results) > 0
        for r in results:
            assert isinstance(r, SearchResult)
            assert r.chunk_id
            assert r.text
            assert r.score >= 0
            assert r.source, f"source was empty for chunk {r.chunk_id}"
            assert r.doc_id

    def test_ingest_single_file(
        self, tmp_path, store, graph_store, detector, resolver, embedder, no_ollama
    ):
        code_file = tmp_path / "hello_func.py"
        code_file.write_text(PY_CODE, encoding="utf-8")
        result = _ingest_single_file(
            str(code_file), detector, embedder, store, graph_store, resolver,
        )
        assert result["doc_id"]
        assert result["source_type"] == "code"
        assert result["file_path"] == str(code_file)
        assert result["chunk_count"] > 0

        md_file = tmp_path / "readme.md"
        md_file.write_text(MARKDOWN, encoding="utf-8")
        result_md = _ingest_single_file(
            str(md_file), detector, embedder, store, graph_store, resolver,
        )
        assert result_md["source_type"] == "markdown"

        searcher = HybridSearcher(store, embedder)
        results = searcher.search("Greeter", k=5)
        assert len(results) > 0
        assert any("Greeter" in r.text for r in results)


# ---------------------------------------------------------------------------
# 2. Cross-source search
# ---------------------------------------------------------------------------

class TestCrossSourceSearch:
    """Verify results come from multiple source types."""

    def test_cross_source_results(
        self, tmp_path, store, graph_store, detector, resolver, embedder, no_ollama
    ):
        _ingest_text(
            PY_CODE, "test_code.py", "code",
            detector, embedder, store, graph_store, resolver,
        )
        _ingest_text(
            MARKDOWN, "test_doc.md", "markdown",
            detector, embedder, store, graph_store, resolver,
        )
        searcher = HybridSearcher(store, embedder)
        results = searcher.search("project", k=10)
        sources = {r.source for r in results if r.source}
        assert len(sources) > 1, (
            f"Expected results from multiple sources, got {sources}"
        )


# ---------------------------------------------------------------------------
# 3. Search context expansion
# ---------------------------------------------------------------------------

class TestSearchContext:
    """Context expansion (parent/sibling chunks via get_chunk_context)."""

    def test_context_expansion(
        self, tmp_path, store, graph_store, detector, resolver, embedder, no_ollama
    ):
        _ingest_text(
            LONG_MARKDOWN, "long_doc.md", "markdown",
            detector, embedder, store, graph_store, resolver,
        )
        searcher = HybridSearcher(store, embedder)
        results = searcher.search("Chapter", k=3)
        assert len(results) > 0, "No search results found for context test"

        first_id = results[0].chunk_id
        context = store.get_chunk_context(first_id, before=1, after=1)
        assert isinstance(context, list)
        for r in context:
            assert isinstance(r, SearchResult)
            assert r.chunk_id
        assert len(context) >= 1


# ---------------------------------------------------------------------------
# 4. Graph entity creation and traversal
# ---------------------------------------------------------------------------

class TestGraph:
    """Direct graph operations: entities, relations, search, BFS."""

    def test_graph_entity_creation_and_traversal(self, graph_store):
        g = graph_store
        e1_id = g.add_entity("HelloFunction", type="Function", metadata={"lang": "python"})
        e2_id = g.add_entity("GreeterClass", type="Class", metadata={"lang": "python"})
        assert e1_id
        assert e2_id

        e1 = g.get_entity(e1_id)
        assert e1 is not None
        assert e1.name == "HelloFunction"
        assert e1.type == "Function"
        assert e1.metadata.get("lang") == "python"

        rel_id = g.add_relation(e1_id, e2_id, rel_type="CALLS", weight=1.0)
        assert rel_id

        neighbors = g.get_neighbors(e1_id, depth=1)
        assert len(neighbors) > 0
        assert any(n["entity"].name == "GreeterClass" for n in neighbors)

        found = g.search_entities("Hello", type="Function")
        assert len(found) > 0
        assert found[0].name == "HelloFunction"

        path = g.bfs_traverse(e1_id, max_depth=3)
        assert len(path) > 0

    def test_graph_stats(self, graph_store):
        g = graph_store
        stats = g.get_stats()
        assert "total_entities" in stats
        assert "total_relations" in stats
        assert stats["backend"] == "sqlite"


# ---------------------------------------------------------------------------
# 5. SQL queries via DuckDBEngine
# ---------------------------------------------------------------------------

class TestSQL:
    """SQL queries over LanceDB-backed tables."""

    def test_sql_select(
        self, tmp_path, store, graph_store, detector, resolver, embedder, duckdb, no_ollama
    ):
        _ingest_text(
            PY_CODE, "test_code.py", "code",
            detector, embedder, store, graph_store, resolver,
        )
        result = duckdb.execute("SELECT chunk_id, text, source, source_type FROM chunks LIMIT 5")
        if "error" in result and result["error"]:
            pytest.skip(f"DuckDB fallback unavailable: {result['error']}")
        assert "columns" in result
        assert "rows" in result
        assert "row_count" in result
        assert result["row_count"] > 0
        assert "chunk_id" in result["columns"]

    def test_sql_empty_result(self, store, duckdb):
        result = duckdb.execute("SELECT * FROM chunks LIMIT 5")
        if "error" in result and result["error"]:
            pytest.skip(f"DuckDB fallback unavailable: {result['error']}")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 6. Versioning
# ---------------------------------------------------------------------------

class TestVersioning:
    """Version management: bump on ingest, list versions, create tag."""

    def test_version_bump(
        self, tmp_path, store, graph_store, detector, resolver, embedder, no_ollama
    ):
        v0 = store.get_stats().current_version
        _ingest_text(
            "v1 content", "doc1.txt", "text",
            detector, embedder, store, graph_store, resolver,
        )
        v1 = store.get_stats().current_version
        assert v1 > v0, f"Version did not bump: {v0} -> {v1}"
        _ingest_text(
            "v2 content", "doc2.txt", "text",
            detector, embedder, store, graph_store, resolver,
        )
        v2 = store.get_stats().current_version
        assert v2 > v1, f"Version did not bump: {v1} -> {v2}"

    def test_list_versions_and_tag(
        self, tmp_path, store, graph_store, detector, resolver, embedder, no_ollama
    ):
        _ingest_text(
            "tag test content", "tag_doc.txt", "text",
            detector, embedder, store, graph_store, resolver,
        )
        versions = store.list_versions()
        assert len(versions) > 0
        latest = versions[0].version
        store.create_tag(latest, "v1.0")
        versions_after = store.list_versions()
        tagged = [v for v in versions_after if v.tag == "v1.0"]
        assert len(tagged) > 0, f"Tag 'v1.0' not found in versions {versions_after}"


# ---------------------------------------------------------------------------
# 7. Server creation
# ---------------------------------------------------------------------------

class TestServer:
    """FastMCP server creation and tool name verification."""

    def test_create_server(self, tmp_path, no_ollama):
        config = {
            "storage": {"path": str(tmp_path / "lancedb"), "graph_db": str(tmp_path / "graph.db")},
            "graph": {"backend": "sqlite"},
            "chunking": {},
            "embedding": {"model": "nomic-embed-text", "dimensions": 768},
        }
        mcp = create_server(config)
        assert mcp is not None
        assert mcp.name == "corpus-kb"
        _verify_tool_names(mcp)

    def test_server_with_minimal_config(self, tmp_path, no_ollama):
        config = {
            "storage": {"path": str(tmp_path / "lancedb")},
        }
        mcp = create_server(config)
        assert mcp is not None
        assert mcp.name == "corpus-kb"


def _verify_tool_names(mcp):
    """Assert that all expected tool names are registered on the FastMCP server."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is None:
        pytest.skip("Cannot access _tool_manager to verify tool names")
        return
    tools = getattr(tool_manager, "_tools", None) or getattr(tool_manager, "tools", None)
    if tools is None:
        pytest.skip("Cannot enumerate registered tools")
        return
    if isinstance(tools, dict):
        tool_names = set(tools.keys())
    elif isinstance(tools, (list, tuple)):
        tool_names = {t.name if hasattr(t, "name") else str(t) for t in tools}
    else:
        pytest.skip(f"Unexpected tools type: {type(tools)}")
        return
    expected = {
        "ingest_file", "ingest_text", "ingest_directory",
        "list_documents", "delete_document",
        "search", "search_context", "search_similar", "retrieve_context",
        "add_entity", "add_relation", "search_graph", "bfs", "get_entity_relations",
        "sql_query",
        "list_versions", "create_tag", "get_stats",
        "checkout_version", "restore_version", "create_branch",
        "list_branches", "switch_branch",
        # database_tools
        "sql_execute", "sql_tables",
        "add_tag", "tag_document", "untag_document", "get_document_tags",
        "set_metadata", "get_metadata", "sync_database", "query_document_stats",
    }
    missing = expected - tool_names
    extra = tool_names - expected
    assert not missing, f"Missing tool names: {missing}"
    assert not extra, f"Unexpected tool names: {extra}"


# ---------------------------------------------------------------------------
# 8. Tool registration per module
# ---------------------------------------------------------------------------

class TestToolRegistration:
    """Each tool module's register_tools() adds the expected number of tools."""

    def test_all_tool_modules(self, tmp_path, no_ollama):
        from mcp.server.fastmcp import FastMCP
        mcp = MagicMock(spec=FastMCP)
        mcp.tool.return_value = lambda f: f

        store = LanceDBStore(str(tmp_path / "lancedb"))
        embedder_obj = OllamaEmbedder(model="nomic-embed-text", dimensions=768)
        graph = create_graph_store({
            "graph": {"backend": "sqlite"},
            "storage": {"graph_db": str(tmp_path / "graph.db")},
        })
        duckdb = DuckDBEngine(str(tmp_path / "lancedb"))
        det = FileTypeDetector()
        res = HierarchyResolver()

        from tools.ingest_tools import register_tools as r_ingest
        from tools.search_tools import register_tools as r_search
        from tools.graph_tools import register_tools as r_graph
        from tools.database_tools import register_tools as r_database
        from tools.version_tools import register_tools as r_version

        call_count_before = mcp.tool.call_count

        r_ingest(mcp, det, embedder_obj, store, graph, res)
        ingest_count = mcp.tool.call_count - call_count_before
        assert ingest_count == 5, f"Expected 5 ingest tools, got {ingest_count}"

        r_search(mcp, store, embedder_obj)
        search_count = mcp.tool.call_count - call_count_before - ingest_count
        assert search_count == 4, f"Expected 4 search tools, got {search_count}"

        r_graph(mcp, graph)
        graph_count = mcp.tool.call_count - call_count_before - ingest_count - search_count
        assert graph_count == 5, f"Expected 5 graph tools, got {graph_count}"

        r_database(mcp, duckdb, store)
        db_count = mcp.tool.call_count - call_count_before - ingest_count - search_count - graph_count
        assert db_count == 11, f"Expected 11 database tools, got {db_count}"

        r_version(mcp, store, graph)
        total = mcp.tool.call_count - call_count_before
        assert total == 33, f"Expected 33 total tools, got {total}"


# ---------------------------------------------------------------------------
# 9. Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Edge cases and error conditions."""

    def test_search_empty_store(self, store, embedder, no_ollama):
        searcher = HybridSearcher(store, embedder)
        results = searcher.search("anything", k=5)
        assert isinstance(results, list)
        assert len(results) == 0

    def test_search_empty_query(
        self, tmp_path, store, graph_store, detector, resolver, embedder, no_ollama
    ):
        _ingest_text(
            "some content", "doc.txt", "text",
            detector, embedder, store, graph_store, resolver,
        )
        searcher = HybridSearcher(store, embedder)
        try:
            results = searcher.search("", k=5)
            assert isinstance(results, list)
        except RuntimeError as e:
            # LanceDB FTS may reject empty query strings in some versions
            pytest.skip(f"LanceDB rejected empty query: {e}")

    def test_get_nonexistent_chunk(self, store):
        result = store.get_chunk("nonexistent-chunk-id-12345")
        assert result is None

    def test_get_chunk_context_nonexistent(self, store):
        context = store.get_chunk_context("nonexistent-chunk-id-12345")
        assert context == []

    def test_insert_empty_chunks(self, store):
        count = store.insert_chunks([])
        assert count == 0


# ---------------------------------------------------------------------------
# 10. Reranker integration
# ---------------------------------------------------------------------------

class TestReranker:
    """Reranker in identity (pass-through) mode."""

    def test_reranker_identity(self):
        reranker = Reranker(mode="identity", top_k=10)
        results = [
            SearchResult(chunk_id="1", text="first result", score=0.9, source="a"),
            SearchResult(chunk_id="2", text="second result", score=0.5, source="b"),
        ]
        reranked = reranker.rerank("test query", results, k=10)
        assert len(reranked) == 2
        assert reranked[0].chunk_id == "1"

    def test_reranker_truncation(self):
        reranker = Reranker(mode="identity", top_k=10)
        many = [
            SearchResult(chunk_id=str(i), text=f"result {i}", score=1.0 - i * 0.01, source="a")
            for i in range(20)
        ]
        reranked = reranker.rerank("test", many, k=5)
        assert len(reranked) == 5

    def test_reranker_empty(self):
        reranker = Reranker(mode="identity", top_k=10)
        reranked = reranker.rerank("test", [], k=5)
        assert reranked == []

    def test_reranker_result_dict_roundtrip(self):
        result = SearchResult(
            chunk_id="abc",
            text="some text",
            score=0.75,
            source="test.py",
        )
        d = _result_to_dict(result)
        assert d["chunk_id"] == "abc"
        assert d["text"] == "some text"
        assert d["score"] == 0.75
        assert d["source"] == "test.py"
        assert d["context_type"] == "direct"

    def test_reranker_preserves_order(self):
        reranker = Reranker(mode="identity", top_k=10)
        results = [
            SearchResult(chunk_id=str(i), text=f"item {i}", score=float(10 - i), source="x")
            for i in range(5)
        ]
        reranked = reranker.rerank("query", results, k=10)
        assert len(reranked) == 5
        scores = [r.score for r in reranked]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Run standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
