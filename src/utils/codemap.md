# src/utils/

## Responsibility

Single module (`models.py`) — 8 dataclasses serving as the shared data contract across every layer of the system. No business logic, no I/O. Pure data structures with two serialization methods on `Chunk` and `Document` for LanceDB round-tripping.

## Design

All models are Python `@dataclass` with `field(default_factory=...)` for mutable defaults. UUID generation and timestamps are module-level helper functions (`_uuid()`, `_now()`).

### Chunk

The central model. Carries content, vector, hierarchy, and provenance.

| Field | Type | Purpose |
|-------|------|---------|
| `chunk_id` | `str` | UUID, auto-generated |
| `doc_id` | `str` | Parent document reference (set post-creation during ingest) |
| `text` | `str` | Raw chunk content |
| `vector` | `Optional[list[float]]` | Embedding vector, attached by `OllamaEmbedder` |
| `chunk_index` | `int` | Position within document |
| `source` | `str` | Original file path or source identifier |
| `source_type` | `str` | `text` | `code` | `markdown` |
| `metadata` | `dict` | Arbitrary key-value store (JSON-serialized for LanceDB) |
| `heading_path` | `list[str]` | Markdown heading ancestry (resolved by `HierarchyResolver`) |
| `parent_chunk_id` | `Optional[str]` | Parent chunk reference for hierarchy |
| `sibling_order` / `sibling_count` | `int` | Sibling positioning within parent |
| `scope_chain` | `list[str]` | Code scope ancestry (module → class → function → method) |
| `chunk_type` | `str` | `function` | `class` | `method` | `section` | `paragraph` |
| `entity_name` | `Optional[str]` | Extracted code entity (function/class name) |
| `file_path` | `Optional[str]` | Source file path |
| `start_line` / `end_line` | `Optional[int]` | Source location |
| `created_at` | `str` | ISO 8601 timestamp |

**Serialization**: `to_lance()` converts to LanceDB-compatible dict (JSON-serializes `metadata`, `heading_path`, `scope_chain`). `from_lance()` reverses the process, strips LanceDB internal fields (`_distance`, `_relevance`), and filters to known dataclass fields.

### Document

Lightweight document-level metadata. No serialization round-trip — only `to_lance()` for storage.

| Field | Type | Purpose |
|-------|------|---------|
| `doc_id` | `str` | UUID, auto-generated |
| `source` | `str` | File path or source identifier |
| `source_type` | `str` | `text` | `code` | `markdown` |
| `metadata` | `dict` | Arbitrary metadata (file size, language, etc.) |
| `chunk_count` | `int` | Number of chunks produced |
| `created_at` | `str` | ISO 8601 timestamp |

### SearchResult

Read-only view of a chunk returned by search. No vector, no mutable state.

| Field | Type | Purpose |
|-------|------|---------|
| `chunk_id` | `str` | Reference to source chunk |
| `text` | `str` | Chunk content |
| `score` | `float` | RRF-fused relevance score |
| `source` | `str` | Document source |
| `doc_id` | `str` | Parent document |
| `chunk_type` | `str` | Type of chunk |
| `entity_name` | `Optional[str]` | Code entity if applicable |
| `heading_path` | `list[str]` | Markdown heading path |
| `scope_chain` | `list[str]` | Code scope chain |
| `file_path` | `Optional[str]` | Source file |
| `start_line` / `end_line` | `Optional[int]` | Source location |
| `context_type` | `str` | `direct` | `parent` | `sibling` (for context expansion) |
| `metadata` | `dict` | Additional metadata |

### Entity / Relation

Graph nodes and edges.

**Entity**: `entity_id` (UUID), `name`, `type` (`class` | `function` | `concept` | `person` | `place`), `metadata`, `created_at`.

**Relation**: `relation_id` (UUID), `source_id`, `target_id`, `relation_type` (`CALLS` | `DEPENDS_ON` | `CONTAINS` | `related_to`), `weight` (0.0-1.0), `metadata`, `created_at`.

### Version / Branch

LanceDB versioning metadata.

**Version**: `version` (int), `timestamp` (str), `tag` (optional str).

**Branch**: `name` (str), `version` (int — fork point), `created_at` (str).

### Stats

Aggregate database statistics.

| Field | Type | Purpose |
|-------|------|---------|
| `total_documents` | `int` | Document count |
| `total_chunks` | `int` | Chunk count |
| `total_entities` | `int` | Graph entity count |
| `total_relations` | `int` | Graph relation count |
| `db_size_bytes` | `int` | Storage size |
| `current_version` | `int` | Latest LanceDB version |
| `storage_path` | `str` | Data directory path |

## Flow

### Ingest Pipeline

```
File content → chunker.chunk() → Chunk (no vector, no doc_id)
             → resolver.resolve() → Chunk (heading_path, scope_chain, parent_chunk_id populated)
             → embedder.embed_chunks() → Chunk (vector attached)
             → doc = Document(...) → store.insert_document(doc)
             → chunk.doc_id = doc.doc_id → store.insert_chunks(chunks)
```

### Search Pipeline

```
Query → embedder.embed() → vector
      → store.search_hybrid() → Chunk objects
      → SearchResult(chunk_id, text, score, ...) → _result_to_dict() → MCP response
```

### Graph Pipeline

```
Ingest: Chunk.entity_name → graph.add_entity(name=entity_name, type=chunk_type) → Entity
MCP: add_entity(name, type) → Entity → stored in GraphStore
MCP: add_relation(source_id, target_id) → Relation → stored in GraphStore
```

### Version Pipeline

```
store.insert_chunks() → LanceDB auto-creates version → Version(version, timestamp)
MCP: list_versions() → Version list → MCP response
MCP: create_tag(version, tag_name) → Version tagged
```

## Integration

Every module in the system imports from `models.py`:

| Consumer | Models Used |
|----------|-------------|
| `storage.lancedb_store` | `Chunk`, `Document`, `Version`, `Stats` |
| `storage.duckdb_engine` | `Document` (metadata sync), `Chunk` (relational sync) |
| `storage.graph_store` | `Entity`, `Relation` |
| `chunking.code_chunker` | `Chunk` (output) |
| `chunking.markdown_chunker` | `Chunk` (output) |
| `chunking.text_chunker` | `Chunk` (output) |
| `chunking.hierarchy` | `Chunk` (mutates heading_path, parent_chunk_id, scope_chain) |
| `rag.embedder` | `Chunk` (attaches vector) |
| `rag.hybrid_search` | `Chunk` → `SearchResult` (conversion) |
| `tools.ingest_tools` | `Chunk`, `Document` |
| `tools.search_tools` | `SearchResult` |
| `tools.graph_tools` | `Entity`, `Relation` (implicit via GraphStore) |
| `tools.version_tools` | `Version`, `Stats` |
