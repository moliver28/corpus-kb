# src/storage/

## Responsibility

Three storage backends serving distinct query patterns within the Corpus-KB RAG pipeline:

- **LanceDB** — Primary vector store. Persists chunk embeddings, supports vector/FTS/hybrid search, and provides immutable versioning (time-travel, branches, tags).
- **DuckDB** — Persistent relational SQL layer. Mirrors LanceDB documents/chunks into normalized tables, adds tags, document_tags, and a flexible key-value metadata store. Full SQL support with safety rails on writes.
- **GraphStore** — Entity-relation knowledge graph. Abstract interface with pluggable backends: SQLite (Level 1, zero deps) and GraphQLite (Level 2, Cypher + graph algorithms).

## Design

### LanceDBStore (`lancedb_store.py`)

Single class managing two PyArrow tables (`documents`, `chunks`) within a LanceDB database.

**Schema**: `documents` holds doc_id, source, source_type, metadata (JSON string), chunk_count, created_at. `chunks` holds chunk_id, doc_id, text, vector (float32 list, configurable dimensions), chunk_index, source_type, metadata, heading_path, parent_chunk_id, sibling_order, sibling_count, scope_chain, chunk_type, entity_name, file_path, start_line, end_line, created_at.

**Search operations**:
- `search_vector(query_embedding, k, filters)` — ANN vector search with optional SQL WHERE filters built via `_build_where()`.
- `search_fts(query_text, k, filters)` — Full-text search via LanceDB's native FTS index on the `text` column. Gracefully returns empty if FTS index is missing.
- `search_hybrid(query_text, query_embedding, k, filters, rrf_k)` — Runs both searches at `k * 2`, then fuses rankings via `_rrf_fuse()` using Reciprocal Rank Fusion with configurable k constant (default 60).

**Versioning**: Every `add()` call creates an immutable version. `list_versions()` returns version history with tag resolution. `checkout(version)` for read-only time-travel, `restore(version)` creates a new commit rolling back state. Tags are created via `tbl.tags.create()`. Branches are implemented as `branch:`-prefixed tags pointing to a version, with `switch_branch()` checking out the tagged version.

**Internals**: `_resolve_uri()` expands `~` paths. `_safe_json_load()` handles malformed JSON in metadata fields. `_to_search_result()` converts LanceDB rows to `SearchResult` Pydantic models. FTS index is created at table initialization via `create_fts_index("text", replace=True)`.

### DuckDBEngine (`duckdb_engine.py`)

File-backed DuckDB connection (`corpus.db` by default) with five relational tables.

**Schema**:
- `documents` — doc_id (PK), source, source_type, chunk_count, file_size, file_hash, language, created_at, updated_at, metadata_json.
- `chunks` — chunk_id (PK), doc_id (FK), chunk_index, source_type, chunk_type, entity_name, heading_path, file_path, start_line, end_line, char_count, created_at.
- `tags` — tag_id (PK), name (UNIQUE), color, description, created_at.
- `document_tags` — (doc_id, tag_id) composite PK, both FKs.
- `metadata` — id (PK), key, value, doc_id, UNIQUE(key, doc_id).

**Sync**: `sync_from_lancedb(lancedb_store)` pulls all documents and chunks from LanceDB via `INSERT OR REPLACE`, extracting `file_size` and `language` from the LanceDB metadata JSON. Idempotent — safe to call repeatedly.

**SQL execution**: `execute(sql)` parses the SQL string to block `DROP TABLE/DATABASE/SCHEMA` and unbounded `DELETE`/`UPDATE` (requires WHERE clause). Returns `{columns, rows, row_count}` for SELECT, `{affected_rows}` for writes. `execute_safe(sql, params)` wraps parameterized queries for injection prevention.

**Tag/metadata helpers**: `add_tag()`, `tag_document()` (auto-creates tag if missing), `untag_document()`, `get_document_tags()`, `set_metadata()`, `get_metadata()` with optional key/doc_id filtering.

### GraphStore (`graph_store.py`)

Abstract base class (`GraphStore`) with concrete `SQLiteGraphStore` implementation and factory `create_graph_store(config)`.

**Abstract interface**: `add_entity()`, `add_relation()`, `get_entity()`, `get_neighbors()`, `search_entities()`, `bfs_traverse()`, `get_stats()`. Level 2+ methods (`cypher_query()`, `pagerank()`, `louvain()`, `shortest_path()`) raise `NotImplementedError` by default.

**SQLiteGraphStore**: Two tables — `entities` (entity_id PK, name, type, metadata JSON, created_at) and `relations` (relation_id PK, source_id FK, target_id FK, relation_type, weight, metadata JSON, created_at). WAL journal mode, foreign keys enabled. Indexes on relations(source_id), relations(target_id), relations(relation_type), entities(name).

**BFS traversal**: Uses a recursive CTE (`WITH RECURSIVE traverse AS ...`) with anchor on the start entity and recursive expansion up to `max_depth`. Prevents immediate backtracking via `e.entity_id != t.source_entity_id`.

**GraphQLiteGraphStore**: Dynamically constructed class inside `_create_graphqlite_store()`. Wraps `graphqlite.Graph` with `upsert_node()`/`upsert_edge()` for CRUD, Cypher MATCH queries for traversal, and native `pagerank()`/`louvain()`/`dijkstra()` for algorithms. Import is deferred — no dependency at Level 1.

**Factory**: `create_graph_store(config)` reads `graph.backend` from config dict. Routes to `SQLiteGraphStore`, `GraphQLiteGraphStore`, or raises `NotImplementedError` for `latticedb`.

## Flow

```
Files / Directories / Raw Text
         │
         ▼
   Chunking Layer (code/markdown/text)
         │
         ▼
   ┌─────────────────────────────────────────────┐
   │  LanceDBStore.insert_document()             │
   │  LanceDBStore.insert_chunks()               │
   │  → vectors + text stored in LanceDB tables  │
   │  → automatic version commit                 │
   └──────────────────┬──────────────────────────┘
                      │
                      ▼
   ┌─────────────────────────────────────────────┐
   │  DuckDBEngine.sync_from_lancedb()           │
   │  → INSERT OR REPLACE into documents/chunks  │
   │  → extracts file_size, language from meta   │
   └─────────────────────────────────────────────┘
                      │
                      ▼ (optional, during ingest)
   ┌─────────────────────────────────────────────┐
   │  GraphStore.add_entity() / add_relation()   │
   │  → entities extracted from chunk metadata   │
   │  → relations (CALLS, DEPENDS_ON, CONTAINS)  │
   └─────────────────────────────────────────────┘
```

Data enters through the chunking layer, which produces `Chunk` and `Document` Pydantic models. LanceDB is the primary write target — every insert creates a new immutable version. DuckDB sync is a downstream mirror operation, typically called after ingest to populate relational tables. Graph entities are populated separately during ingest when code chunking extracts entity names and scope chains.

Search flows in the opposite direction: queries hit `search_hybrid()` on LanceDBStore, which runs parallel vector and FTS searches, fuses rankings via RRF, and returns `SearchResult` objects. Context expansion (`get_chunk_context()`) retrieves adjacent chunks by `chunk_index` within the same document.

## Integration

| Consumer | Backend Used | Key Interactions |
|----------|-------------|-----------------|
| `search_tools.py` | LanceDBStore | `search_vector()`, `search_fts()`, `search_hybrid()`, `ensure_fts_index()` |
| `database_tools.py` | DuckDBEngine + LanceDBStore | `execute()`, `sync_from_lancedb()`, tag operations, metadata CRUD, `list_documents()` |
| `sql_tools.py` | DuckDBEngine | `execute()` for raw SQL SELECT, `execute_safe()` for parameterized writes |
| `graph_tools.py` | GraphStore | `add_entity()`, `add_relation()`, `search_entities()`, `bfs_traverse()`, `get_stats()` |
| `version_tools.py` | LanceDBStore + GraphStore | `list_versions()`, `checkout()`, `restore()`, `create_tag()`, `create_branch()`, `switch_branch()` |
| `ingest_tools.py` | All three | `insert_document()`, `insert_chunks()` on LanceDB; `sync_from_lancedb()` on DuckDB; `add_entity()`/`add_relation()` on GraphStore |

All three backends are instantiated in `src/server.py` and injected into tool registration functions. The `__init__.py` re-exports `LanceDBStore`, `DuckDBEngine`, `GraphStore`, `SQLiteGraphStore`, and `create_graph_store` for clean imports.
