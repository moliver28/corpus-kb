"""Corpus-KB Demo — ingests sample files and runs a few queries.

Usage:
    python scripts/demo.py

Requires:
    - pip install -e .  (project installed)
    - ollama pull nomic-embed-text  (Ollama running)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from server import create_server


def main():
    print("=" * 60)
    print("Corpus-KB Demo")
    print("=" * 60)

    # Create server with in-memory-like config (temporary paths)
    with tempfile.TemporaryDirectory() as tmp:
        config = {
            "storage": {"path": os.path.join(tmp, "lancedb")},
            "graph": {"backend": "sqlite", "db_path": os.path.join(tmp, "graph.db")},
            "embedding": {"model": "nomic-embed-text", "dimensions": 768, "batch_size": 32},
        }

        print("\n[1] Initializing server...")
        mcp = create_server(config)
        print("    Server created successfully.")

        # Access tools via the underlying functions
        # (In production, MCP clients call these via stdio/SSE)
        from tools.ingest_tools import _ingest_single_file, _ingest_text
        from tools.search_tools import _result_to_dict
        from storage.lancedb_store import LanceDBStore
        from storage.graph_store import create_graph_store
        from chunking.detector import FileTypeDetector
        from chunking.hierarchy import HierarchyResolver
        from rag.embedder import OllamaEmbedder

        embedder = OllamaEmbedder()
        store = LanceDBStore(config["storage"]["path"], dimensions=embedder.dimensions)
        graph = create_graph_store(config)
        detector = FileTypeDetector()
        resolver = HierarchyResolver()

        # Create sample content
        sample_code = """def hello():
    print("Hello, World!")

class Greeter:
    def greet(self, name):
        return f"Hello, {name}!"

def add(a, b):
    return a + b
"""

        sample_markdown = """# Welcome

## Getting Started

This is a sample document for the demo.

### Installation

Run `pip install corpus-kb` to get started.

## Features

- Hybrid search
- Code chunking
- Knowledge graph
"""

        print("\n[2] Ingesting sample code...")
        result = _ingest_text(
            text=sample_code,
            source="demo.py",
            file_type="code",
            detector=detector,
            embedder=embedder,
            store=store,
            graph=graph,
            resolver=resolver,
        )
        print(f"    Created {result['chunk_count']} chunks (doc_id: {result['doc_id'][:8]}...)")

        print("\n[3] Ingesting sample markdown...")
        result = _ingest_text(
            text=sample_markdown,
            source="demo.md",
            file_type="markdown",
            detector=detector,
            embedder=embedder,
            store=store,
            graph=graph,
            resolver=resolver,
        )
        print(f"    Created {result['chunk_count']} chunks (doc_id: {result['doc_id'][:8]}...)")

        # Search
        from rag.hybrid_search import HybridSearcher

        searcher = HybridSearcher(store, embedder)

        print("\n[4] Searching for 'greeting function'...")
        results = searcher.search("greeting function", k=5)
        print(f"    Found {len(results)} results:")
        for r in results:
            snippet = r.text[:80].replace("\n", " ")
            print(f"    [{r.score:.3f}] {snippet}...")

        print("\n[5] Searching for 'installation guide'...")
        results = searcher.search("installation guide", k=5)
        print(f"    Found {len(results)} results:")
        for r in results:
            snippet = r.text[:80].replace("\n", " ")
            print(f"    [{r.score:.3f}] {snippet}...")

        # SQL
        from storage.duckdb_engine import DuckDBEngine

        db = DuckDBEngine(config["storage"]["path"])
        db.sync_from_lancedb(store)

        print("\n[6] SQL: chunk count by source_type...")
        sql_result = db.execute(
            "SELECT source_type, COUNT(*) as cnt FROM chunks GROUP BY source_type"
        )
        for row in sql_result.get("rows", []):
            print(f"    {row[0]}: {row[1]}")

        # Graph
        print("\n[7] Searching graph entities...")
        entities = graph.search_entities("", type=None)
        for e in entities[:5]:
            print(f"    {e.name} ({e.type})")

        # Stats
        print("\n[8] Database stats...")
        stats = store.get_stats()
        gs = graph.get_stats()
        print(f"    Documents: {stats.total_documents}")
        print(f"    Chunks: {stats.total_chunks}")
        print(f"    Entities: {gs.get('entity_count', 0)}")
        print(f"    Relations: {gs.get('relation_count', 0)}")
        print(f"    Version: {stats.current_version}")

    print("\n" + "=" * 60)
    print("Demo completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
