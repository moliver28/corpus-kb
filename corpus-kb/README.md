# Corpus-KB

[![CI](https://github.com/moliver28/corpus-kb/actions/workflows/ci.yml/badge.svg)](https://github.com/moliver28/corpus-kb/actions/workflows/ci.yml)

**Local RAG system for AI code editors. Ingest your codebase. Ask questions. Get answers. No cloud.**

Corpus-KB brings retrieval-augmented generation (RAG) to your local machine. It ingests code files, documentation, and plain text, then serves them up via MCP (Model Context Protocol) tools that any AI editor can call. Think of it as a private search engine for your codebase that your AI coding assistant can query in real time.

---

## Quick Start

```bash
# 1. Install PostgreSQL 17 with pgvector + Apache AGE
#    See docs/W0_POSTGRES_SETUP.md for detailed setup

# 2. Install the package
pip install -e ".[postgres,dev]"

# 3. Pull the embedding model (Ollama must be running)
ollama pull nomic-embed-text

# 4. Load the database schema
psql -U corpus_user -d corpus_kb -f src/storage/schema.sql

# 5. Start the server
python -m src.server_wiring --transport http --port 8010

# 6. Ingest your codebase via HTTP API
curl -X POST http://localhost:8010/api/ingest/directory \
  -H "Content-Type: application/json" \
  -d '{"directory_path":"src","recursive":true}'

# 7. Search
curl -X POST http://localhost:8010/api/search \
  -H "Content-Type: application/json" \
  -d '{"query":"authentication","k":10}'
```

---

## What Is This?

RAG stands for Retrieval-Augmented Generation. It's a pattern where an AI model doesn't just rely on its training data. Instead, it searches a local knowledge base (your code, your docs) and uses what it finds to ground its answers. No hallucinations about your private code. No data leaving your machine.

Corpus-KB implements this as an **MCP server** with three protocols: MCP stdio (for editor agents), HTTP REST API (Starlette, 24 routes), and JSON-RPC socket. Any MCP-compatible client (OpenCode, Claude Code, Cursor, VS Code with Cline) can connect.

```
+-----------------------------------------------------------+
|                   Your AI Code Editor                      |
|  (OpenCode / Claude Code / Cursor / VS Code + Cline)      |
+---------------------+-------------------------------------+
                      | MCP (stdio) or HTTP (port 8010)
+---------------------v-------------------------------------+
|                   Corpus-KB Server                         |
|                                                           |
|  +----------+  +----------+  +----------+  +---------+   |
|  | Ingest   |  | Search   |  | Graph    |  | Tags    |   |
|  | Tools    |  | Tools    |  | Traverse |  | Metadata|   |
|  +----+-----+  +----+-----+  +----+-----+  +----+----+   |
|       |             |             |             |        |
|  +----v-------------v-------------v-------------v----+  |
|  |              Domain Layer (Event Sourcing)          |  |
|  |  Aggregates -> Events -> Event Store (append-only)  |  |
|  +-----------------------+-----------------------------+  |
|                          |                               |
|  +-----------------------v-----------------------------+ |
|  |           Async Projections                          | |
|  |  EmbedChunksProjection -> pgvector                   | |
|  |  DocumentsProjection -> documents, chunks, entities  | |
|  |  Checkpoint + DLQ for crash recovery                 | |
|  +-----------------------+-----------------------------+ |
|                          |                               |
|  +-----------------------v-----------------------------+ |
|  |              PostgreSQL 17                           | |
|  |  +----------+  +----------+  +----------+           | |
|  |  | pgvector |  | Apache AGE|  | 12 tables|           | |
|  |  | (vector  |  | (Cypher  |  | (RLS on  |           | |
|  |  |  search) |  |  graphs) |  |  all)    |           | |
|  |  +----------+  +----------+  +----------+           | |
|  +------------------------------------------------------+ |
|                          |                               |
|  +-----------------------v-----------------------------+ |
|  |         Embedding Service (Ollama)                  | |
|  |  nomic-embed-text / qwen3-embedding / any Ollama    | |
|  +------------------------------------------------------+ |
+-----------------------------------------------------------+
```

The pipeline works like this:

1. **Ingest** - Feed it files, directories, or raw text. Corpus-KB detects the type (code, markdown, plain text) and splits the content into chunks using the right strategy.
2. **Embed** - Each chunk gets converted to a vector using Ollama running locally. Vectors are stored in pgvector (not in event payloads).
3. **Store** - Events (immutable facts) go to the event store. Projections update read models (documents, chunks, vectors, entities, relations) in Postgres tables.
4. **Search** - When your AI editor asks a question, Corpus-KB runs hybrid search (pgvector cosine distance + Postgres full-text search + Reciprocal Rank Fusion) and returns the most relevant chunks.
5. **Answer** - Your AI editor reads the chunks and answers your question, grounded in your actual code.

---

## Features

**Ingest anything** - Files, directories, or raw text. Auto-detects code (40+ languages via tree-sitter), markdown (heading-aware splitting), and plain text (semantic topic-gap detection). Code chunks respect AST boundaries: no splitting mid-function or mid-class.

**Hybrid search** - pgvector cosine similarity finds conceptually related chunks. Postgres full-text search finds exact keyword matches. Reciprocal Rank Fusion combines both rankings into one result list.

**Relational SQL** - Full Postgres SQL queries over your ingested data. JOIN documents to chunks, GROUP BY source type, filter by metadata. 12 tables with indexes and schema introspection.

**Entity graph** - Apache AGE Cypher graph queries with entities (classes, functions, concepts, people, places) and typed relations (CALLS, DEPENDS_ON, CONTAINS). Includes BFS traversal via recursive CTE.

**Event sourcing** - Every ingest, entity creation, and relation creation fires an immutable event. The event store is an append-only audit log. Projections build read models asynchronously. Crash recovery via checkpoint tracking + dead-letter queue.

**Versioning** - Event sourcing provides native time-travel: replay events up to a version to see the state at that point. No separate versioning table needed.

**Tags + metadata** - Tag documents with colored labels. Store arbitrary key-value metadata per document or globally.

**Configurable embedding** - Switch between nomic-embed-text (768d, default) and qwen3-embedding:8b (4096d) via config.yaml. The embedding_model column tracks which model was used.

**100% local** - Ollama does the embeddings. Postgres runs locally. No OpenAI key, no cloud API. Your code never leaves your machine.

**Multi-tenancy ready** - Row Level Security (RLS) on all 12 tables. Currently uses a single placeholder tenant, but the infrastructure is ready for real multi-tenancy.

---

## Editor Integration

Corpus-KB connects to any MCP-compatible editor via stdio or HTTP transport.

| Client | Transport | Config |
|--------|-----------|--------|
| OpenCode | stdio | `mcp-configs/opencode.json` |
| Claude Code | stdio | `mcp-configs/claude-code.json` |
| Cursor | stdio | `mcp-configs/cursor.json` |
| VS Code (Cline) | stdio | `mcp-configs/cursor.json` |
| Any HTTP client | HTTP | `http://localhost:8010/api/*` |

### MCP Tools Reference (34 tools, 24 HTTP routes)

| Category | Tool | HTTP Route |
|----------|------|-----------|
| Ingest | `ingest_file` | POST /api/ingest/file |
| Ingest | `ingest_text` | POST /api/ingest/text |
| Ingest | `ingest_directory` | POST /api/ingest/directory |
| Ingest | `list_documents` | GET /api/documents |
| Ingest | `delete_document` | DELETE /api/documents/{id} |
| Search | `search` | POST /api/search |
| Search | `search_context` | POST /api/search/context |
| Search | `search_similar` | POST /api/search/similar |
| Search | `retrieve_context` | POST /api/search/context |
| SQL | `sql_query` | POST /api/query/sql |
| SQL | `sql_tables` | GET /api/tables |
| Graph | `add_entity` | POST /api/entities |
| Graph | `add_relation` | POST /api/relations |
| Graph | `search_graph` | POST /api/graph/search |
| Graph | `bfs` | POST /api/graph/bfs |
| Graph | `get_entity_relations` | GET /api/graph/relations/{id} |
| Tags | `add_tag` | POST /api/tags |
| Tags | `tag_document` | POST /api/documents/{id}/tags |
| Tags | `get_document_tags` | GET /api/documents/{id}/tags |
| Metadata | `set_metadata` | POST /api/metadata |
| Metadata | `get_metadata` | GET /api/metadata |
| Version | `list_versions` | GET /api/versions |
| Stats | `get_stats` | GET /api/stats |
| Stats | `query_document_stats` | GET /api/document-stats |

---

## Configuration

All tunable parameters live in `config.yaml` at the project root.

```yaml
server:
  name: corpus-kb
  transport: http              # stdio | http | sse
  host: localhost
  port: 8010

database:
  connection_string: "postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb"

embedding:
  provider: ollama
  model: nomic-embed-text      # or qwen3-embedding:8b-q8_0
  base_url: http://localhost:11434
  batch_size: 32
  dimensions: 768              # 768 for nomic, 4096 for qwen3

chunking:
  max_size: 4096
  overlap: 200

search:
  rrf_k: 60
  expand_context: true

graph:
  extractor: langextract       # regex | langextract | bert
  ontology_path: config/ontology.yaml
```

### Upgrading the embedding model

```bash
ollama pull qwen3-embedding:8b-q8_0
```

Then update `config.yaml`:

```yaml
embedding:
  model: qwen3-embedding:8b-q8_0
  dimensions: 4096
  batch_size: 128
```

---

## Architecture

Corpus-KB uses event sourcing with PostgreSQL 17 as the sole database.

### Storage

- **PostgreSQL 17** - primary database for all data (events, documents, chunks, vectors, entities, relations, tags, metadata)
- **pgvector 0.8.0** - vector similarity search via `vector(4096)` type with ivfflat index
- **Apache AGE 1.5.0** - Cypher graph queries for BFS traversal, pattern matching, path finding
- **Event Store** - append-only event log (eventsourcing library), immutable audit trail
- **Projections** - async event subscribers that build read models (EmbedChunksProjection, DocumentsProjection)
- **RLS** - Row Level Security on all 12 tables for multi-tenant isolation

### 12 Database Tables

| Table | Purpose | RLS |
|-------|---------|-----|
| tenants | tenant management | Yes |
| documents | document metadata | Yes |
| chunks | chunk text + metadata (no vectors) | Yes |
| chunks_vectors | pgvector embeddings (async projection) | Yes |
| entities | knowledge graph nodes | Yes |
| relations | knowledge graph edges | Yes |
| projection_checkpoints | crash recovery for projections | Yes |
| projection_dlq | dead-letter queue for failed projections | Yes |
| idempotency_keys | command deduplication | Yes |
| tags | document tags | Yes |
| document_tags | many-to-many tag-document mapping | Yes |
| metadata | key-value metadata store | Yes |

---

## FAQ

### Do I need PostgreSQL?

Yes. PostgreSQL 17+ with pgvector and Apache AGE extensions is required. See `docs/W0_POSTGRES_SETUP.md` for setup instructions.

### Do I need a GPU?

No. The default model `nomic-embed-text` runs fine on CPU. You only need a GPU if you upgrade to a much larger model.

### What is event sourcing?

Event sourcing is an architecture pattern where every state change is recorded as an immutable event. Instead of updating rows in place, you append events to an event log. Projections then build read models from events. This gives you a complete audit trail, time-travel (replay events to any point), and crash recovery (projections resume from checkpoints).

### Can I use a different embedding model?

Yes. Any model available through Ollama works. Change `config.yaml` with the model name and dimensions, then `ollama pull <model>`. The `embedding_model` column in `chunks_vectors` tracks which model was used.

### Can I run this without Ollama?

Yes, in degraded mode. The embedder returns zero vectors on connection failure. Search will still work via full-text search (FTS), but vector similarity won't be available.

---

## Development

### Project structure

```
corpus-kb/
+-- config.yaml              # All tunable parameters
+-- pyproject.toml           # Python package config
+-- src/
|   +-- server_wiring.py     # Server startup (all protocols)
|   +-- domain/              # Event sourcing domain layer
|   |   +-- aggregates.py    # Document, Entity, Relation aggregates
|   |   +-- application.py    # CorpusApplication (eventsourcing)
|   |   +-- models.py        # Pydantic v2 command/query models
|   +-- handlers/            # Command + query dispatch
|   |   +-- command_handler.py
|   |   +-- query_handler.py
|   |   +-- graph_handler.py
|   |   +-- tag_handler.py
|   |   +-- versioning_handler.py
|   |   +-- idempotency.py
|   |   +-- error_handling.py
|   +-- projections/         # Async event subscribers
|   |   +-- embed_projection.py
|   |   +-- checkpoint.py
|   |   +-- dlq.py
|   |   +-- documents_projection.py
|   +-- api/                 # Protocol adapters
|   |   +-- http.py           # Starlette (24 routes)
|   |   +-- socket.py         # JSON-RPC 2.0
|   +-- storage/
|   |   +-- schema.sql        # Postgres DDL (12 tables, RLS)
|   |   +-- lance_store.py    # Legacy (for migration)
|   +-- extraction/          # Pluggable NER
|   |   +-- protocol.py
|   |   +-- regex_backend.py
|   |   +-- langextract_backend.py
|   |   +-- bert_backend.py   # Minimal spaCy NER
|   +-- tools/
|   |   +-- ingest_common.py  # Pipeline orchestrator
|   +-- ontology.py
|   +-- partitioning.py
|   +-- config.py
+-- scripts/
|   +-- migrate_to_postgres.py
|   +-- validate_migration.py
+-- tests/
+-- docs/
    +-- W0_POSTGRES_SETUP.md
```

### Running tests

```bash
pip install -e ".[dev]"
pytest
```

### Contributing

- Python 3.11+ only. No type: ignore. No `Any` where a real type works.
- 250-line soft limit on source files.
- TDD for new features. Tests go in `tests/` mirroring `src/` structure.
- All imports are relative (no `from src.xxx`).

---

## License

MIT. See [LICENSE](LICENSE) for details.
