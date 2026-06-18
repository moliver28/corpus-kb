# Corpus-KB

Local RAG system with MCP tools for AI code editors. Lets you ingest code and documents, semantically search, run SQL queries, and manage a knowledge graph all locally, no cloud.

## Quickstart

```bash
# 1. Install
pip install -e .

# 2. Pull embedding model (required)
ollama pull nomic-embed-text

# 3. Run the server (stdio mode for editor agents)
python -m src.server

# Or via the installed CLI:
corpus-kb
```

Default config is in `config.yaml` at the project root. On first run, data is stored in `~/.corpus-kb/` (LanceDB + DuckDB + SQLite graph).

## Server

| Transport | Command | Use Case |
|-----------|---------|----------|
| stdio | `corpus-kb` (or `python -m src.server`) | Editor agents (OpenCode, Claude Code, Cursor, Codex) |
| SSE | `corpus-kb --transport sse --port 8000` | Multi-user / remote |

The entry point is `src/server.py`, module `server:main`. Config is loaded from `config.yaml` in `$CWD`, `./corpus-kb/config.yaml`, or `~/.corpus-kb/config.yaml`. Override with `--config <path>` or `CORPUS_KB_CONFIG` env var.

## MCP Tools Reference

All tools are registered via `FastMCP` in `src/tools/`.

### Ingest (5 tools)

| Tool | Description |
|------|-------------|
| `ingest_file` | Ingest a single file, auto-detects code/markdown/text |
| `ingest_text` | Ingest raw text with optional type hint (code/markdown/text) |
| `ingest_directory` | Ingest all supported files in a directory (supports 40+ languages via tree-sitter + .md, .rst, .txt) |
| `list_documents` | List all ingested documents with metadata |
| `delete_document` | Delete an ingested document by doc_id |

Source: `src/tools/ingest_tools.py`

### Search (4 tools)

| Tool | Description |
|------|-------------|
| `search` | Hybrid search (vector + full-text + RRF fusion). Args: query, k (max 50), source_type filter |
| `search_context` | Search with parent/sibling/child chunk context expansion. Args: query, k (max 20), context_chunks (0-5) |
| `search_similar` | Find chunks similar to a given chunk by chunk_id. Args: chunk_id, k (max 20) |
| `retrieve_context` | Search and return results formatted as a string for LLM context building. Args: query, k (max 20), filters (JSON string) |

Source: `src/tools/search_tools.py`

### Knowledge Graph (5 tools)

| Tool | Description |
|------|-------------|
| `add_entity` | Add an entity to the knowledge graph. Args: name, type (default "concept"), metadata |
| `add_relation` | Add a directed relation between two entities. Args: source_id, target_id, rel_type, weight (0.0-1.0) |
| `search_graph` | Search entities by name or type. Args: query, type filter, limit (max 100) |
| `bfs` | BFS traversal from a starting entity. Args: start_entity_id, max_depth (1-10) |
| `get_entity_relations` | Get all relations for an entity (incoming and outgoing) |

Source: `src/tools/graph_tools.py`

### Database / SQL (11 tools)

| Tool | Description |
|------|-------------|
| `sql_query` | Run a SQL SELECT query over relational tables. Full SQL: JOIN, CTE, GROUP BY, window functions, subqueries, UNION |
| `sql_execute` | Execute INSERT/UPDATE/DELETE with safety rails (blocks DROP and bare DELETE/UPDATE) |
| `sql_tables` | List all relational tables with their column schemas |
| `add_tag` | Create a new tag for categorizing documents |
| `tag_document` | Apply a tag to a document (creates tag if missing) |
| `untag_document` | Remove a tag from a document |
| `get_document_tags` | Get all tags applied to a document |
| `set_metadata` | Set a metadata key-value pair, optionally scoped to a document |
| `get_metadata` | Retrieve metadata entries filtered by key and/or doc_id |
| `sync_database` | Manually trigger LanceDB to relational sync (idempotent) |
| `query_document_stats` | Aggregate statistics: total docs/chunks, by type, avg chunk chars, date range |

Relational tables: `documents`, `chunks`, `tags`, `document_tags`, `metadata`.

Source: `src/tools/database_tools.py`

### Versioning / Branching (8 tools)

| Tool | Description |
|------|-------------|
| `list_versions` | List all versions of the chunks table for time-travel |
| `create_tag` | Tag a specific version with a human-readable name |
| `checkout_version` | Check out a specific table version for time-travel queries |
| `restore_version` | Restore the table to a specific version |
| `create_branch` | Create a new branch from an optional version |
| `list_branches` | List all branches |
| `switch_branch` | Switch to a specified branch |
| `get_stats` | Database statistics: document count, chunk count, entity count, relation count, current version |

Source: `src/tools/version_tools.py`

### Resources (read-only URIs)

| URI Pattern | Description |
|-------------|-------------|
| `stats://summary` | Database statistics formatted as text |
| `chunk://{chunk_id}` | Full text content of a chunk |
| `doc://{doc_id}` | Full document info with all its chunks |
| `graph://{entity_id}` | Entity details with all relations |
| `search://{query}` | Search results as formatted text |
| `versions://` | Version tree |

## Editor Config

### OpenCode (`opencode.json`)

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "corpus-kb": {
      "type": "local",
      "command": ["corpus-kb", "--transport", "stdio"],
      "autoApprove": [
        "search", "search_context", "search_similar", "retrieve_context",
        "list_documents", "get_stats", "list_versions", "list_branches",
        "get_entity_relations", "search_graph", "sql_query", "sql_tables",
        "get_document_tags", "get_metadata", "query_document_stats",
        "sync_database"
      ]
    }
  }
}
```

### Claude Code (`claude mcp add` or `.vscode/mcp.json`)

```json
{
  "mcpServers": {
    "corpus-kb": {
      "name": "Corpus-KB",
      "command": "corpus-kb",
      "args": ["--transport", "stdio"],
      "autoApprove": [
        "search", "search_context", "search_similar", "retrieve_context",
        "list_documents", "get_stats", "list_versions", "list_branches",
        "get_entity_relations", "search_graph", "sql_query", "sql_tables",
        "get_document_tags", "get_metadata", "query_document_stats",
        "sync_database"
      ]
    }
  }
}
```

### Cursor

```json
{
  "mcpServers": {
    "corpus-kb": {
      "name": "Corpus-KB",
      "command": "corpus-kb",
      "args": ["--transport", "stdio"]
    }
  }
}
```

## Configuration (`config.yaml`)

```yaml
server:
  name: corpus-kb
  transport: stdio              # stdio | sse
  host: localhost
  port: 8010

storage:
  path: ~/.corpus-kb
  lancedb_uri: ~/.corpus-kb/lancedb
  graph_db: ~/.corpus-kb/graph.db

database:
  filename: corpus.db
  auto_sync: true

embedding:
  provider: ollama
  model: nomic-embed-text       # 768d. Upgrade to qwen3-embedding:8b-q8_0 for 4096d
  base_url: http://localhost:11434
  batch_size: 32
  dimensions: 768

chunking:
  max_size: 4096
  overlap: 200
  code:                         # tree-sitter AST chunking (40+ languages)
    max_size: 5000
  markdown:                     # heading-aware section chunking
    max_section_size: 5000
  text:                         # semantic sentence-boundary chunking
    strategy: semantic

search:
  hybrid: true
  rrf_k: 60
  expand_context: true

graph:
  backend: sqlite               # sqlite | graphqlite
  extract_entities: true
  max_traversal_depth: 5
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  FastMCP Server (src/server.py)                              │
│  stdio / SSE transport                                       │
├─────────────────────────────────────────────────────────────┤
│  Tools Layer (src/tools/)                                    │
│  ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌─────┐ │
│  │ Ingest   │ │ Search   │ │ Graph   │ │ Database │ │Ver  │ │
│  │ 5 tools  │ │ 4 tools  │ │ 5 tools │ │ 11 tools │ │8 tls│ │
│  └────┬─────┘ └────┬─────┘ └────┬────┘ └────┬─────┘ └──┬──┘ │
├───────┴────────────┴────────────┴───────────┴──────────┴────┤
│  Engine Layer                                               │
│  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐ │
│  │ Chunking     │  │ Embedding   │  │ Hybrid Search      │ │
│  │ (tree-sitter │  │ (Ollama)    │  │ (RRF fusion)       │ │
│  │  + markdown  │  │             │  │                    │ │
│  │  + semantic) │  │             │  │                    │ │
│  └──────┬───────┘  └──────┬──────┘  └────────┬───────────┘ │
├─────────┴─────────────────┴──────────────────┴─────────────┤
│  Storage Layer                                              │
│  ┌──────────────┐  ┌────────────┐  ┌──────────────────────┐│
│  │ LanceDB      │  │ DuckDB     │  │ SQLite Graph        ││
│  │ (vectors +   │  │ (relational│  │ (entity-relation    ││
│  │  chunks,     │  │  tables:   │  │  store, BFS,        ││
│  │  versioned)  │  │  docs,     │  │  upgradable)        ││
│  │              │  │  chunks,   │  │                      ││
│  │              │  │  tags,     │  │                      ││
│  │              │  │  metadata) │  │                      ││
│  └──────────────┘  └────────────┘  └──────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

Three storage backends, each with a distinct role:
- **LanceDB** stores chunk vectors and full text with Git-like versioning (branches, tags, time-travel).
- **DuckDB** mirrors document and chunk metadata into relational tables for ad-hoc SQL queries, tags, and flexible metadata.
- **SQLite Graph** tracks entities and relations for knowledge graph traversal (BFS). Upgradable to GraphQLite (Cypher) or LatticeDB.

The chunking pipeline detects file type (code/markdown/text), splits accordingly (tree-sitter AST for code, heading-aware for markdown, semantic for text), resolves parent/child/sibling hierarchy, embeds via Ollama, and stores in LanceDB. Auto-sync populates DuckDB on startup and after each ingest.
