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

import json
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

    @mcp.resource("doc://{doc_id}")
    def doc_resource(doc_id: str) -> str:
        """Full document info with all its chunks."""
        doc = store.get_document(doc_id)
        if not doc:
            return f"Document not found: {doc_id}"

        chunks = store.chunks_table.search().where(
            f"doc_id = '{doc_id}'"
        ).limit(10000).to_list()

        parts = [
            f"# Document: {doc.get('source', 'unknown')}\n",
            f"- **ID**: {doc_id}",
            f"- **Source Type**: {doc.get('source_type', 'unknown')}",
            f"- **Chunk Count**: {doc.get('chunk_count', len(chunks))}",
            f"- **Created**: {doc.get('created_at', 'unknown')}",
        ]

        if doc.get("metadata"):
            meta = doc["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta)
            if meta:
                parts.append(f"- **Metadata**: {meta}")

        parts.append(f"\n## Chunks ({len(chunks)})\n")

        for c in sorted(chunks, key=lambda x: x.get("chunk_index", 0)):
            idx = c.get("chunk_index", 0)
            text = c.get("text", "")
            ctype = c.get("chunk_type", "paragraph")
            parts.append(f"### Chunk [{idx}] ({ctype})")
            if text:
                parts.append(f"{text}\n")

        return "\n".join(parts)

    @mcp.resource("graph://{entity_id}")
    def graph_resource(entity_id: str) -> str:
        """Entity details with all relations."""
        entity = graph.get_entity(entity_id)
        if not entity:
            return f"Entity not found: {entity_id}"

        meta = entity.metadata or {}

        parts = [
            f"# Entity: {entity.name}",
            f"- **ID**: {entity.entity_id}",
            f"- **Type**: {entity.type}",
            f"- **Created**: {entity.created_at}",
        ]
        if meta:
            parts.append(f"- **Metadata**: {meta}")

        neighbors = graph.get_neighbors(entity_id)
        if neighbors:
            parts.append(f"\n## Relations ({len(neighbors)})\n")
            for n in neighbors:
                ent = n.get("entity", {})
                rel = n.get("relation", {})
                if hasattr(ent, "name"):
                    ent_name = ent.name
                elif isinstance(ent, dict):
                    ent_name = ent.get("name", "unknown")
                else:
                    ent_name = str(ent)
                rtype = rel.get("relation_type", "related_to") if isinstance(rel, dict) else "related_to"
                direction = rel.get("direction", "") if isinstance(rel, dict) else ""
                parts.append(f"- **{direction}** [{rtype}] → {ent_name}")
        else:
            parts.append("\nNo relations found.")

        return "\n".join(parts)

    @mcp.resource("search://{query}")
    def search_resource(query: str) -> str:
        """Search results as formatted text."""
        vector = embedder.embed(query)
        results = store.search_vector(vector, k=10)

        parts = [f"# Search Results: {query}\n"]
        for i, r in enumerate(results):
            parts.append(
                f"### Result [{i}] (score: {r.score:.4f})\n"
                f"- **Source**: {r.source}\n"
                f"- **Doc**: {r.doc_id}\n"
                f"- **Type**: {r.chunk_type}\n"
                f"{r.text}\n"
            )

        return "\n".join(parts)

    @mcp.resource("versions://")
    def versions_resource() -> str:
        """Version tree."""
        versions = store.list_versions()

        parts = ["# Version Tree\n"]
        for v in versions:
            tag = f" ({v.tag})" if v.tag else ""
            parts.append(f"- **Version {v.version}**{tag} — {v.timestamp}")

        if not versions:
            parts.append("No versions available.")

        return "\n".join(parts)

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
