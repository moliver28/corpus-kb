# src/tools/

## Responsibility

34 MCP tools across 6 modules, each exposing a `register_tools(mcp, ...)` function that decorates plain Python functions with `@mcp.tool()` for FastMCP registration.

| Module | Tools | Responsibility |
|--------|-------|----------------|
| `ingest_tools.py` (5) | `ingest_file`, `ingest_text`, `ingest_directory`, `list_documents`, `delete_document` | File/directory/text ingestion pipeline: detect type → chunk → embed → store → graph → relational sync |
| `search_tools.py` (4) | `search`, `search_context`, `search_similar`, `retrieve_context` | Hybrid retrieval: vector + FTS + RRF fusion, context expansion, similarity-by-ID, LLM-formatted output |
| `graph_tools.py` (5) | `add_entity`, `add_relation`, `search_graph`, `bfs`, `get_entity_relations` | Knowledge graph CRUD: entity/relation creation, name/type search, BFS traversal, neighbor inspection |
| `database_tools.py` (11) | `sql_query`, `sql_execute`, `sql_tables`, `add_tag`, `tag_document`, `untag_document`, `get_document_tags`, `set_metadata`, `get_metadata`, `sync_database`, `query_document_stats` | Relational SQL engine: full SELECT/CTE/JOIN queries, parameterized writes with safety rails, tag/metadata management, LanceDB→DuckDB sync |
| `version_tools.py` (7) | `list_versions`, `create_tag`, `get_stats`, `checkout_version`, `restore_version`, `create_branch`, `list_branches`, `switch_branch` | LanceDB versioning: immutable version history, tagging, time-travel checkout/restore, branch creation/switching |
| `sql_tools.py` (1) | `sql_query` | Lightweight SQL query wrapper over DuckDB (subset of `database_tools.py` functionality, lower default limit) |

## Design

### Tool Registration Pattern

Every module exports a single `register_tools(mcp, ...)` function. Dependencies (stores, embedders, detectors) are injected at registration time and captured in closures. Each tool is a plain function decorated with `@mcp.tool()`, relying on FastMCP to extract the signature, docstring, and parameter types for the MCP tool schema.

```
register_tools(mcp, store, embedder, ...)
  └─ @mcp.tool()
     def tool_name(param: type) -> dict:
         ...
```

### Parameter Validation

Tools enforce bounds at call time, not at schema level:
- `k` capped per tool: `search` → 50, `search_context` → 20, `search_similar` → 20, `retrieve_context` → 20, `search_graph` → 100, `bfs` depth → 1-10, `sql_query` limit → 5000 (database_tools) / 1000 (sql_tools)
- `context_chunks` clamped to 0-5
- `weight` for relations: float 0.0-1.0 (accepted as-is, no clamp)

### Error Handling

- **Ingest**: `FileNotFoundError` / `NotADirectoryError` raised for invalid paths. Per-file errors in `ingest_directory` caught and returned as `{file_path, error}` dicts — one failure doesn't abort the batch.
- **Search**: `search_similar` returns `{"error": "Chunk not found: ..."}` for missing IDs. `retrieve_context` returns error string for invalid JSON filters.
- **Graph**: `get_entity_relations` deduplicates by `relation_id` using a `seen` set.
- **Version**: All async version tools (`checkout_version`, `restore_version`, `create_branch`, `list_branches`, `switch_branch`) wrap store calls in try/except, returning `{status: "error", message: ...}` on failure.
- **Database**: `sql_tables` catches all exceptions and returns `{"error": ...}`. `sql_execute` delegates safety (no-WHERE DELETE/UPDATE, DROP blocking) to `DuckDBEngine`.

### Async Tools

Version tools use `async def` despite performing synchronous store operations underneath — a FastMCP compatibility pattern. All other tools are synchronous.

## Flow

### Ingest Flow

```
MCP request → ingest_file(text, directory)
  └─ FileTypeDetector.detect_file_type() → chunker selection
  └─ chunker.chunk(content) → raw chunks
  └─ HierarchyResolver.resolve() → chunks with parent/sibling/scope
  └─ OllamaEmbedder.embed_chunks() → vectors attached
  └─ LanceDBStore.insert_document() + insert_chunks()
  └─ GraphStore.add_entity() for each chunk.entity_name
  └─ DuckDBEngine.sync_from_lancedb() (non-blocking, silent on failure)
  └─ return {doc_id, source, source_type, chunk_count}
```

### Search Flow

```
MCP request → search(query, k, source_type)
  └─ OllamaEmbedder.embed(query) → query vector
  └─ HybridSearcher.search() → vector + FTS + RRF fusion
  └─ Reranker.rerank() → identity pass-through (configurable)
  └─ _result_to_dict() → SearchResult → serializable dict
  └─ return list[dict]
```

### Graph Flow

```
MCP request → add_entity(name, type, metadata)
  └─ GraphStore.add_entity() → entity_id generated
  └─ return {entity_id, name, type}

MCP request → bfs(start_entity_id, max_depth)
  └─ GraphStore.bfs_traverse() → list of {entity_id, name, type, depth, relations}
  └─ return list[dict]
```

### Version Flow

```
MCP request → list_versions()
  └─ LanceDBStore.list_versions() → list[Version]
  └─ return [{version, timestamp, tag}]

MCP request → restore_version(version)
  └─ LanceDBStore.restore(version) → table rolled back
  └─ return {version, status: "restored"}
```

## Integration

### Consumes

| Layer | Modules | Used By |
|-------|---------|---------|
| Storage | `storage.lancedb_store.LanceDBStore` | All tools (ingest, search, version, database sync) |
| Storage | `storage.duckdb_engine.DuckDBEngine` | database_tools, sql_tools |
| Storage | `storage.graph_store.GraphStore` | graph_tools, version_tools (stats), ingest_tools (entity creation) |
| RAG | `rag.embedder.OllamaEmbedder` | ingest_tools, search_tools |
| RAG | `rag.hybrid_search.HybridSearcher` | search_tools |
| RAG | `rag.reranker.Reranker` | search_tools |
| Chunking | `chunking.detector.FileTypeDetector`, `detect_file_type` | ingest_tools |
| Chunking | `chunking.hierarchy.HierarchyResolver` | ingest_tools |
| Utils | `utils.models.Chunk`, `Document`, `SearchResult`, `Entity`, `Relation`, `Version`, `Stats` | All tools (I/O serialization) |

### Called By

- `src/server.py` — FastMCP server entry point. Imports all six `register_tools` functions, instantiates storage/RAG/chunking components from `config.yaml`, and calls each `register_tools(mcp, ...)` to wire tools into the MCP server.
