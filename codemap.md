# corpus-kb/ — Root Codemap

## Project Responsibility

Corpus-KB is a local RAG (Retrieval-Augmented Generation) system that exposes 34 MCP (Model Context Protocol) tools to AI code editors. It ingests code files, documentation, and plain text — auto-detecting file type and splitting content using AST-aware, heading-aware, or semantic strategies — then stores embeddings in LanceDB, mirrors metadata into DuckDB for SQL queries, and populates a SQLite-backed entity graph. Any MCP-compatible client (OpenCode, Claude Code, Cursor, VS Code + Cline) connects via stdio or SSE transport and can search, query, traverse, and version the knowledge base. 100% local: Ollama handles embeddings, no cloud API, no data leaves the machine.

## System Entry Points

| File | Responsibility |
|------|---------------|
| `src/server.py` | FastMCP entrypoint and CLI. Wires all layers (storage, chunking, RAG, graph), registers 34 tools + 6 resources, supports `--transport stdio|sse` and `--port`. Entry point for `corpus-kb` command. |
| `config.yaml` | All tunable parameters: server transport, storage paths, embedding model/dimensions/batch size, chunking strategies per file type, search RRF constants, graph backend, database auto-sync. |
| `pyproject.toml` | Package metadata, dependencies (mcp, lancedb, duckdb, ollama, tree-sitter + language packages, pydantic, rich), optional deps (graphqlite, dev), build system, CLI script registration. |
| `scripts/setup.sh` | macOS/Linux auto-install: installs Python deps, pulls embedding model, creates data directories, copies MCP configs, runs demo smoke test. |
| `scripts/setup.ps1` | Windows PowerShell equivalent of setup.sh — zero-config install. |
| `scripts/demo.py` | End-to-end smoke test: ingests sample files, runs search, verifies pipeline. |

## Directory Map (Aggregated)

| Directory | Responsibility Summary | Detailed Map |
|-----------|----------------------|---------------|
| `src/` | Application source: server factory, layer wiring, tool registration, MCP resources. | [View Map](src/codemap.md) |
| `src/chunking/` | File-type detection and content splitting. Strategy pattern with CodeChunker (AST-aware via tree-sitter, 40+ languages), MarkdownChunker (heading-boundary aware), TextChunker (semantic gap detection). HierarchyResolver assigns parent/sibling relationships. | [View Map](src/chunking/codemap.md) |
| `src/storage/` | Three storage backends: LanceDB (vector store + versioning + hybrid search), DuckDB (relational SQL with 5 tables, tags, metadata), GraphStore (abstract interface with SQLite L1 + GraphQLite L2). | [View Map](src/storage/codemap.md) |
| `src/rag/` | Retrieval pipeline: OllamaEmbedder (batching, SHA256 cache, zero-vector fallback), HybridSearcher (vector + FTS + RRF fusion), Reranker (identity pass-through + optional LLM reranking). | [View Map](src/rag/codemap.md) |
| `src/tools/` | 34 MCP tools across 6 modules: ingest (5), search (4), graph (5), database/SQL (11), versioning (8), sql compat (1). Each exports `register_tools(mcp, ...)` for FastMCP decoration. | [View Map](src/tools/codemap.md) |
| `src/utils/` | Shared data models: Chunk, Document, SearchResult, Entity, Relation, Version, Branch, Stats. Pure dataclasses with serialization methods for LanceDB round-tripping. | [View Map](src/utils/codemap.md) |
| `scripts/` | Setup automation and demo smoke test. | [View Map](scripts/codemap.md) |

## Data Flow Overview

```
File / Directory / Raw Text
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  FileTypeDetector (detector.py)                         │
│  Extension lookup → shebang fallback → default "text"   │
│  Routes to: CodeChunker | MarkdownChunker | TextChunker │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Chunking Layer                                         │
│  CodeChunker:    tree-sitter AST → entities, imports,   │
│                  scope chains, merge pass               │
│  MarkdownChunker: heading boundaries, frontmatter,      │
│                  heading_path stack                     │
│  TextChunker:    paragraph mode or semantic gap detect  │
│                  (sentence embeddings + cosine gaps)    │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  HierarchyResolver                                      │
│  Assigns parent_chunk_id, sibling_order, sibling_count  │
│  Heading-path parentage (markdown) / containment (code) │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  OllamaEmbedder (embedder.py)                           │
│  SHA256 cache → batch ollama.embed() → attach vectors   │
│  Graceful degradation: zero vectors on connection fail  │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Storage Layer                                          │
│                                                         │
│  LanceDBStore                                           │
│  ├─ insert_document() → documents table                │
│  ├─ insert_chunks()  → chunks table (vectors + text)   │
│  └─ auto-commits new immutable version                  │
│                                                         │
│  DuckDBEngine (sync_from_lancedb)                       │
│  ├─ INSERT OR REPLACE → documents, chunks              │
│  ├─ tags, document_tags, metadata tables               │
│  └─ Full SQL: CTEs, JOINs, window functions            │
│                                                         │
│  GraphStore (during ingest)                             │
│  ├─ add_entity() for each chunk.entity_name            │
│  └─ add_relation() for CALLS/DEPENDS_ON/CONTAINS       │
└─────────────────────────────────────────────────────────┘
                       │
                       ▼ (query path)
┌─────────────────────────────────────────────────────────┐
│  Search Pipeline                                        │
│                                                         │
│  Query → OllamaEmbedder.embed() → query vector          │
│        → HybridSearcher.search()                        │
│           ├─ vector_search(k=20) → cosine distance      │
│           ├─ fts_search(k=20)    → BM25                 │
│           └─ RRF fusion (k=60)   → combined ranking     │
│        → Reranker.rerank() → identity (or LLM mode)     │
│        → list[SearchResult] → MCP client                │
└─────────────────────────────────────────────────────────┘
```

## Key Numbers

| Metric | Value |
|--------|-------|
| MCP Tools | 34 (ingest 5, search 4, graph 5, database/SQL 11, versioning 8, sql compat 1) |
| MCP Resources | 6 (stats://summary, chunk://, doc://, graph://, search://, versions://) |
| Tests | 73 (chunking 32, rag 18, integration 23) |
| Languages (tree-sitter) | 40+ (Python, JS/TS, Rust, Go, Java, C/C++, Ruby, PHP, Swift, Kotlin, Scala, Lua, Haskell, Elixir, Clojure) |
| Storage Backends | 3 (LanceDB vectors, DuckDB SQL, SQLite graph) |
| Embedding Models | Any Ollama model (default: nomic-embed-text 768d; upgrade: qwen3-embedding:8b-q8_0 4096d) |
| Cloud Dependencies | 0 — 100% local, no Docker required |
