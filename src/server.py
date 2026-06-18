"""Corpus-KB MCP Server — FastMCP entrypoint.

Wires together storage, chunking, RAG, and graph layers into a single
MCP server with:
- Tools: ingest_file, ingest_text, ingest_directory, search,
  search_context, add_entity, add_relation, search_graph, bfs,
  get_entity_relations, sql_query, list_versions, create_tag, get_stats
- Resources: stats summary, chunk content, document content

Usage:
    corpus-kb                          # stdio transport (for editor agents)
    corpus-kb --transport sse           # SSE transport (for multi-user)
    corpus-kb --transport sse --port 8000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATHS = [
    Path.cwd() / "config.yaml",
    Path.cwd() / "corpus-kb" / "config.yaml",
    Path.home() / ".corpus-kb" / "config.yaml",
]


def load_config() -> dict:
    """Load config from first available path."""
    for path in DEFAULT_CONFIG_PATHS:
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f) or {}
    return {}


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def create_server(config: dict | None = None) -> FastMCP:
    """Create and configure the Corpus-KB MCP server."""
    cfg = config or load_config()

    storage_path = cfg.get("storage", {}).get("path", "./data/lancedb")
    graph_path = cfg.get("graph", {}).get("db_path", "./data/graph.db")
    graph_backend = cfg.get("graph", {}).get("backend", "sqlite")
    chunking_config = cfg.get("chunking", {})
    embedding_config = cfg.get("embedding", {})

    # ------------------------------------------------------------------
    # Initialize layers
    # ------------------------------------------------------------------

    from storage.lancedb_store import LanceDBStore
    from storage.duckdb_engine import DuckDBEngine
    from storage.graph_store import create_graph_store
    from chunking.detector import FileTypeDetector
    from chunking.hierarchy import HierarchyResolver
    from rag.embedder import OllamaEmbedder

    # Storage
    store = LanceDBStore(storage_path)
    duckdb = DuckDBEngine(storage_path)

    # Graph
    graph_config = {"graph": {"backend": graph_backend, "path": graph_path}}
    graph = create_graph_store(graph_config)

    # Chunking
    detector = FileTypeDetector()
    resolver = HierarchyResolver()

    # Embedding
    embedder = OllamaEmbedder(
        model=embedding_config.get("model", "nomic-embed-text"),
        dimensions=embedding_config.get("dimensions", 768),
        batch_size=embedding_config.get("batch_size", 10),
    )

    # ------------------------------------------------------------------
    # Create FastMCP server
    # ------------------------------------------------------------------

    mcp = FastMCP(
        "corpus-kb",
        instructions="""# Corpus-KB: Local RAG for Agentic Code Editors

This server provides a complete local RAG (Retrieval-Augmented Generation)
system for agentic code editors via MCP.

## Key Capabilities

1. **Ingest**: Files (auto-detects code/markdown/text), raw text, directories
2. **Search**: Hybrid search (vector + full-text + RRF fusion)
3. **Graph**: Entity-relation knowledge graph with BFS traversal
4. **SQL**: Full SQL queries over the RAG data via DuckDB
5. **Versioning**: Time-travel, tags, and database statistics

## Quick Start

1. `ingest_file` to add a code file or document
2. `search` to find relevant chunks
3. `search_context` for expanded context around results
4. `sql_query` for analytical queries
5. `add_entity` + `add_relation` to build a knowledge graph
""",
    )

    # ------------------------------------------------------------------
    # Register tools and resources
    # ------------------------------------------------------------------

    from tools.ingest_tools import register_tools as reg_ingest
    from tools.search_tools import register_tools as reg_search
    from tools.graph_tools import register_tools as reg_graph
    from tools.sql_tools import register_tools as reg_sql
    from tools.version_tools import register_tools as reg_version

    reg_ingest(mcp, detector, embedder, store, graph, resolver)
    reg_search(mcp, store, embedder)
    reg_graph(mcp, graph)
    reg_sql(mcp, duckdb)
    reg_version(mcp, store, graph)

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    @mcp.resource("stats://summary")
    def stats_summary() -> str:
        """Database statistics."""
        from utils.models import Stats
        s = store.get_stats()
        gs = graph.get_stats()
        return (
            f"## Corpus-KB Statistics\n\n"
            f"- **Documents**: {s.total_documents}\n"
            f"- **Chunks**: {s.total_chunks}\n"
            f"- **Entities**: {gs.get('entity_count', 0)}\n"
            f"- **Relations**: {gs.get('relation_count', 0)}\n"
            f"- **Version**: {s.current_version}\n"
            f"- **Storage**: {s.storage_path}\n"
        )

    @mcp.resource("chunk://{chunk_id}")
    def chunk_resource(chunk_id: str) -> str:
        """Get a chunk's full text content by ID."""
        chunk = store.get_chunk(chunk_id)
        if not chunk:
            return f"Chunk not found: {chunk_id}"
        return chunk.get("text", "")

    return mcp


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    """Entry point for the corpus-kb CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="Corpus-KB MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", type=str, help="Path to config YAML")
    args = parser.parse_args()

    config = None
    if args.config:
        with open(args.config) as f:
            config = yaml.safe_load(f) or {}
    elif "CORPUS_KB_CONFIG" in os.environ:
        with open(os.environ["CORPUS_KB_CONFIG"]) as f:
            config = yaml.safe_load(f) or {}

    mcp = create_server(config)

    if args.transport == "sse":
        mcp.run(transport="sse", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
