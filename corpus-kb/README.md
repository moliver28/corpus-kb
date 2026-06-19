# Corpus-KB

[![CI](https://github.com/moliver28/corpus-kb/actions/workflows/ci.yml/badge.svg)](https://github.com/moliver28/corpus-kb/actions/workflows/ci.yml)

**Local RAG system for AI code editors. Ingest your codebase. Ask questions. Get answers. No cloud.**

CI Pipeline: Validates linting (ruff), type checking (pyright), and test discovery (pytest) on every push.

Corpus-KB brings retrieval-augmented generation (RAG) to your local machine. It ingests code files, documentation, and plain text, then serves them up via MCP (Model Context Protocol) tools that any AI editor can call. Think of it as a private search engine for your codebase that your AI coding assistant can query in real time.

---

## Quick Start

Five commands from clone to querying your codebase:

```bash
# 1. Install the package
pip install -e .

# 2. Pull the embedding model (Ollama must be running)
ollama pull nomic-embed-text

# 3. Start the MCP server
corpus-kb

# 4. In a separate terminal, run the demo
python scripts/demo.py

# 5. Or start ingesting your own code
corpus-kb  # then use MCP tools to ingest_file, search, sql_query...
```

**Windows users** can run the automated setup script for a zero-config install:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

**macOS / Linux:**

```bash
bash scripts/setup.sh
```

The setup scripts install Python deps, pull the embedding model, create data directories, copy MCP configs into your editor, and run the demo smoke test. About 2 minutes end to end.

---

## What Is This?

RAG stands for Retrieval-Augmented Generation. It's a pattern where an AI model doesn't just rely on its training data. Instead, it searches a local knowledge base (your code, your docs) and uses what it finds to ground its answers. No hallucinations about your private code. No data leaving your machine.

Corpus-KB implements this as an **MCP server**. MCP (Model Context Protocol) is an open standard that lets AI editors talk to tools and data sources. Any MCP-compatible client (OpenCode, Claude Code, Cursor, VS Code with Cline, and others) can connect to Corpus-KB and use its tools.

```
ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ
Γöé                   Your AI Code Editor                   Γöé
Γöé  (OpenCode / Claude Code / Cursor / VS Code + Cline)    Γöé
ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓö¼ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ
                      Γöé MCP (stdio or SSE)
ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓû╝ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ
Γöé                   Corpus-KB Server                      Γöé
Γöé                                                         Γöé
Γöé  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  Γöé
Γöé  Γöé Ingest   Γöé  Γöé Search   Γöé  Γöé SQL      Γöé  Γöé Graph   Γöé  Γöé
Γöé  Γöé Tools    Γöé  Γöé Tools    Γöé  Γöé Queries  Γöé  Γöé TraverseΓöé  Γöé
Γöé  ΓööΓöÇΓöÇΓöÇΓöÇΓö¼ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓö¼ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓö¼ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓö¼ΓöÇΓöÇΓöÇΓöÇΓöÿ  Γöé
Γöé       Γöé             Γöé             Γöé             Γöé       Γöé
Γöé  ΓöîΓöÇΓöÇΓöÇΓöÇΓû╝ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓû╝ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓû╝ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓû╝ΓöÇΓöÇΓöÇΓöÇΓöÉ  Γöé
Γöé  Γöé              Storage Layer                        Γöé  Γöé
Γöé  Γöé  LanceDB (vectors) + DuckDB (SQL) + SQLite (graph)Γöé  Γöé
Γöé  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  Γöé
Γöé       Γöé                                                 Γöé
Γöé  ΓöîΓöÇΓöÇΓöÇΓöÇΓû╝ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  Γöé
Γöé  Γöé         Embedding Service (Ollama)                Γöé  Γöé
Γöé  Γöé  nomic-embed-text / qwen3-embedding / any Ollama  Γöé  Γöé
Γöé  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  Γöé
ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ
```

The pipeline works like this:

1. **Ingest** - Feed it files, directories, or raw text. Corpus-KB detects the type (code, markdown, plain text) and splits the content into chunks using the right strategy.
2. **Embed** - Each chunk gets converted to a vector (a list of numbers that captures meaning) using Ollama running locally.
3. **Store** - Vectors go into LanceDB for similarity search. Document metadata goes into DuckDB for SQL queries. Entities and relations go into the graph store.
4. **Search** - When your AI editor asks a question, Corpus-KB runs hybrid search (vector similarity + full-text keyword search + Reciprocal Rank Fusion) and returns the most relevant chunks.
5. **Answer** - Your AI editor reads the chunks and answers your question, grounded in your actual code.

---

## Features

**Ingest anything** - Files, directories, or raw text. Auto-detects code (40+ languages via tree-sitter), markdown (heading-aware splitting), and plain text (semantic topic-gap detection). Code chunks respect AST boundaries: no splitting mid-function or mid-class.

**Hybrid search** - Vector similarity finds conceptually related chunks. Full-text search finds exact keyword matches. Reciprocal Rank Fusion combines both rankings into one result list. You get the best of both approaches.

**Relational SQL** - Full DuckDB-backed SQL queries over your ingested data. JOIN documents to chunks, GROUP BY source type, filter by metadata. Five relational tables with indexes and schema introspection.

**Entity graph** - SQLite-backed knowledge graph with entities (classes, functions, concepts, people, places) and typed relations (CALLS, DEPENDS_ON, CONTAINS). Includes BFS traversal. Upgradable to GraphQLite for Cypher queries, PageRank, Louvain community detection, and shortest paths.

**Versioning** - LanceDB gives you Git-like versioning out of the box. Every write creates a new version. List versions, tag them, time-travel back to see what your codebase looked like at any point. Create branches for experimental ingest runs.

**100% local** - Ollama does the embeddings. No OpenAI key, no cloud API, no Docker required. Your code never leaves your machine.

---

## Editor Integration

Corpus-KB connects to any MCP-compatible editor via stdio or SSE transport. Config files live in `mcp-configs/`.

| Client | Config File | Notes |
|--------|-------------|-------|
| OpenCode | `mcp-configs/opencode.json` | Auto-approved read-only tools by default |
| Claude Code | `mcp-configs/claude-code.json` | Installs to `~/.claude/mcp.json` |
| Cursor | `mcp-configs/cursor.json` | Installs to `.vscode/mcp.json` |
| VS Code (Cline) | `mcp-configs/cursor.json` | Same format as Cursor |
| Windsurf | `mcp-configs/cursor.json` | Same format as Cursor |

Each config points the editor's MCP client at the `corpus-kb` server binary. The setup scripts rewrite these configs to use the absolute path inside your virtual environment.

**OpenCode** config example:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "corpus-kb": {
      "type": "local",
      "description": "Local RAG system for your codebase",
      "command": ["corpus-kb", "--transport", "stdio"],
      "environment": {},
      "autoApprove": [
        "search", "search_context", "search_similar",
        "retrieve_context", "list_documents", "get_stats",
        "list_versions", "list_branches", "get_entity_relations",
        "search_graph", "sql_query", "sql_tables",
        "get_document_tags", "get_metadata", "query_document_stats",
        "sync_database"
      ]
    }
  }
}
```

**Claude Code** config (`~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "corpus-kb": {
      "name": "Corpus-KB",
      "command": "corpus-kb",
      "args": ["--transport", "stdio"],
      "env": {}
    }
  }
}
```

### MCP Tools Reference

| Category | Tool | Description |
|----------|------|-------------|
| Ingest | `ingest_file` | Ingest a single file (auto-detects type) |
| Ingest | `ingest_text` | Ingest raw text with optional type hint |
| Ingest | `ingest_directory` | Ingest all supported files in a directory |
| Ingest | `list_documents` | List all ingested documents |
| Ingest | `delete_document` | Delete a document by ID |
| Search | `search` | Hybrid search (vector + FTS + RRF) |
| Search | `search_context` | Search with parent/sibling/child context expansion |
| Search | `search_similar` | Find chunks similar to a given chunk |
| Search | `retrieve_context` | Search results formatted for LLM context building |
| SQL | `sql_query` | Full SQL SELECT over documents, chunks, tags, metadata |
| SQL | `sql_execute` | Parameterized INSERT/UPDATE/DELETE with safety rails |
| SQL | `sql_tables` | List all tables with their schemas |
| SQL | `sync_database` | Sync LanceDB data into relational tables |
| Graph | `add_entity` | Add an entity to the knowledge graph |
| Graph | `add_relation` | Add a relation between two entities |
| Graph | `search_graph` | Search entities by name or type |
| Graph | `bfs` | BFS traversal from a starting entity |
| Graph | `get_entity_relations` | Get all relations for an entity |
| Version | `list_versions` | List all table versions |
| Version | `create_tag` | Tag a version for reference |
| Version | `checkout_version` | Time-travel to a specific version |
| Version | `restore_version` | Restore to a specific version |
| Version | `create_branch` | Create a new branch |
| Version | `list_branches` | List all branches |
| Version | `switch_branch` | Switch to a branch |
| Tags | `add_tag` | Create a new tag |
| Tags | `tag_document` | Apply a tag to a document |
| Tags | `untag_document` | Remove a tag from a document |
| Tags | `get_document_tags` | List tags on a document |
| Metadata | `set_metadata` | Set a metadata key-value pair |
| Metadata | `get_metadata` | Retrieve metadata entries |
| Stats | `get_stats` | Database statistics |
| Stats | `query_document_stats` | Aggregate document statistics |

---

## Configuration

All tunable parameters live in `config.yaml` at the project root. Here's what each section does:

```yaml
# Corpus-KB: Configuration
server:
  name: corpus-kb
  transport: stdio              # stdio | http
  host: localhost
  port: 8010

storage:
  path: ~/.corpus-kb            # Data directory
  lancedb_uri: ~/.corpus-kb/lancedb
  graph_db: ~/.corpus-kb/graph.db

embedding:
  provider: ollama
  model: nomic-embed-text        # ~274 MB, 768d. Good default.
  base_url: http://localhost:11434
  batch_size: 32                 # Safe batch size for most models
  dimensions: 768                # Must match your model

chunking:
  max_size: 4096
  overlap: 200
  code:
    enabled: true
    max_size: 5000               # Large entities fit in one chunk
    parser: tree-sitter
    supported_languages: [python, javascript, typescript, rust, go, ...]
  markdown:
    enabled: true
    heading_levels: [1, 2, 3]
    max_section_size: 5000
  text:
    enabled: true
    strategy: semantic           # Uses embedding model for topic-gap detection
    min_sentences: 3
    max_sentences: 40

search:
  hybrid: true
  rrf_k: 60                     # RRF constant (higher = more weight to FTS)
  expand_context: true          # Include parent/sibling chunks in results

graph:
  backend: sqlite               # sqlite | graphqlite | latticedb (future)
  extract_entities: true
  max_traversal_depth: 5

database:
  filename: corpus.db            # Persistent DuckDB file
  auto_sync: true                # Auto-sync LanceDB to relational on startup
```

### Upgrading the embedding model

Better embeddings mean better search results. The default `nomic-embed-text` (768d, ~274 MB) works well for most codebases. For higher quality, try the Qwen3 embedding model:

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

This model is MTEB #1 ranked and gives noticeably better semantic understanding. It needs about 8 GB of RAM.

---

## Architecture

Corpus-KB is a layered system. Each layer has a clear responsibility and can be swapped or upgraded independently.

```
ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ
Γöé                    MCP Interface (FastMCP)                  Γöé
Γöé   stdio transport (editors)  |  SSE transport (network)    Γöé
Γö£ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöñ
Γöé                     Tool Layer                              Γöé
Γöé                                                             Γöé
Γöé  ingest_tools.py    search_tools.py    graph_tools.py      Γöé
Γöé  database_tools.py  version_tools.py   sql_tools.py        Γöé
Γö£ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöñ
Γöé                      RAG Layer                              Γöé
Γöé                                                             Γöé
Γöé  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  Γöé
Γöé  Γöé  OllamaEmbedderΓöé  Γöé HybridSearcher Γöé  Γöé  Reranker    Γöé  Γöé
Γöé  Γöé  - embed()     Γöé  Γöé - vector searchΓöé  Γöé - identity   Γöé  Γöé
Γöé  Γöé  - embed_batch Γöé  Γöé - FTS search   Γöé  Γöé - LLM (opt)  Γöé  Γöé
Γöé  Γöé  - embed_chunksΓöé  Γöé - RRF fusion   Γöé  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  Γöé
Γöé  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ                    Γöé
Γö£ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöñ
Γöé                    Chunking Layer                           Γöé
Γöé                                                             Γöé
Γöé  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  Γöé
Γöé  Γöé CodeChunker  Γöé  ΓöéMarkdownChunkerΓöé Γöé  TextChunker     Γöé  Γöé
Γöé  Γöé (tree-sitter)Γöé  Γöé (heading-aware)Γöé Γöé (semantic gaps) Γöé  Γöé
Γöé  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  Γöé
Γöé  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ                        Γöé
Γöé  ΓöéFileTypeDetectΓöé  ΓöéHierarchyRes  Γöé                        Γöé
Γöé  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ                        Γöé
Γö£ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöñ
Γöé                    Storage Layer                            Γöé
Γöé                                                             Γöé
Γöé  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÉ  Γöé
Γöé  Γöé LanceDB  Γöé  Γöé DuckDB   Γöé  Γöé GraphStoreΓöé  Γöé Ollama    Γöé  Γöé
Γöé  Γöé (vectors)Γöé  Γöé (SQL)    Γöé  Γöé (SQLite  Γöé  Γöé (embeddingΓöé  Γöé
Γöé  Γöé (version)Γöé  Γöé (tags)   Γöé  Γöé  /GraphQLΓöé  Γöé  service) Γöé  Γöé
Γöé  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ  Γöé
ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ
```

### Storage backends

Three storage engines, each optimized for a different query pattern:

- **LanceDB** - Columnar vector database. Stores chunk embeddings and supports vector search, full-text search (via LanceDB FTS), and hybrid search with RRF. Built-in versioning: every write creates a new immutable version you can time-travel to.

- **DuckDB** - Embedded analytical SQL engine. Stores document metadata, chunk records, tags, and a flexible key-value metadata store. Full SQL support including CTEs, window functions, JOINs across all tables. Auto-syncs from LanceDB on startup and after each ingest.

- **SQLite / GraphQLite** - Entity-relation graph. Level 1 ships with SQLite (zero extra dependencies). Level 2 upgrades to GraphQLite for Cypher queries, PageRank, Louvain community detection, and shortest paths. The abstract `GraphStore` interface means MCP tools never change.

### Embedding service

Corpus-KB uses Ollama's embedding API. The embedder caches results in memory (SHA256-keyed, LRU eviction at 10,000 entries) and batches requests for efficiency. On connection failure, it returns zero vectors for graceful degradation.

---

## FAQ

### Do I need a GPU?

No. The default model `nomic-embed-text` runs fine on CPU. It's about 274 MB and produces 768-dimensional embeddings. On a modern laptop, embedding a typical project takes seconds. You only need a GPU if you upgrade to a much larger model.

### Can I use OpenAI embeddings instead of Ollama?

Currently Corpus-KB uses Ollama as its embedding provider. The embedder is a thin class that calls Ollama's HTTP API. You could swap it for OpenAI's embedding API with a small adapter. Pull requests welcome.

### How is this different from RAG on a vector database like Pinecone?

Pinecone and other cloud vector databases send your data to a third party. Corpus-KB is 100% local. No data ever leaves your machine. It also has three storage backends (vector, relational, graph), not just vector search. You can run SQL queries across your chunks and traverse entity relationships, which you cannot do with a pure vector store.

### How is this different from code search tools like ripgrep?

Ripgrep is a regex search tool. It finds exact string matches. Corpus-KB finds conceptually related code even when the keywords are different. Search for "authentication" and it can find code using JWT, OAuth, sessions, and login flows. It also understands code structure (functions, classes, methods) rather than treating code as flat text.

### What languages does tree-sitter support for code chunking?

Python, JavaScript, TypeScript, JSX, TSX, Rust, Go, Java, C++, C, Ruby, PHP, Swift, Kotlin, Scala, Lua, Haskell, Elixir, Clojure. That's 40+ languages through tree-sitter grammar packages. Code files in unsupported languages fall back to line-based chunking that still respects function and class boundaries heuristically.

### Can I use a different embedding model?

Yes. Any model available through Ollama works. Just change `config.yaml` with the model name and dimensions, then `ollama pull <model>`. The default `nomic-embed-text` is a solid starting point. `qwen3-embedding:8b-q8_0` is the current leader for quality.

### How do I version my data?

LanceDB versions every write automatically. Run `list_versions` to see the version history. Use `checkout_version` to time-travel to any version (read-only). Use `restore_version` to roll back. Use `create_tag` to mark important versions (like "before-refactor" or "v1.0"). Branches let you experiment with different ingest strategies.

### Can I run this without Ollama?

No, Ollama is required for embeddings. It's the component that converts text into vectors that enable semantic search. Ollama is free, open source, and runs entirely on your machine. The setup scripts install it automatically.

### Does it work with large codebases?

Yes. LanceDB handles millions of vectors efficiently. DuckDB is built for analytical queries over large datasets. The chunking engine is streaming-friendly. For very large repos, ingest the whole directory at once with `ingest_directory` and let the pipeline batch-process everything.

---

## Development

### Project structure

```
corpus-kb/
Γö£ΓöÇΓöÇ config.yaml              # All tunable parameters
Γö£ΓöÇΓöÇ pyproject.toml           # Python package config
Γö£ΓöÇΓöÇ scripts/
Γöé   Γö£ΓöÇΓöÇ demo.py              # Quick demo / smoke test
Γöé   Γö£ΓöÇΓöÇ setup.ps1            # Windows auto-install
Γöé   ΓööΓöÇΓöÇ setup.sh             # macOS / Linux auto-install
Γö£ΓöÇΓöÇ mcp-configs/
Γöé   Γö£ΓöÇΓöÇ opencode.json        # OpenCode MCP config
Γöé   Γö£ΓöÇΓöÇ claude-code.json     # Claude Code MCP config
Γöé   ΓööΓöÇΓöÇ cursor.json          # Cursor / VS Code MCP config
Γö£ΓöÇΓöÇ src/
Γöé   Γö£ΓöÇΓöÇ server.py            # FastMCP entrypoint, CLI
Γöé   Γö£ΓöÇΓöÇ chunking/            # File-type detection and chunking
Γöé   Γöé   Γö£ΓöÇΓöÇ code_chunker.py  # AST-aware (tree-sitter)
Γöé   Γöé   Γö£ΓöÇΓöÇ markdown_chunker.py
Γöé   Γöé   Γö£ΓöÇΓöÇ text_chunker.py  # Semantic gap detection
Γöé   Γöé   Γö£ΓöÇΓöÇ detector.py      # File type -> chunker routing
Γöé   Γöé   ΓööΓöÇΓöÇ hierarchy.py     # Parent/sibling relationships
Γöé   Γö£ΓöÇΓöÇ storage/
Γöé   Γöé   Γö£ΓöÇΓöÇ lancedb_store.py # Vector store with versioning
Γöé   Γöé   Γö£ΓöÇΓöÇ duckdb_engine.py # Relational SQL engine
Γöé   Γöé   ΓööΓöÇΓöÇ graph_store.py   # Entity graph (SQLite + GraphQLite)
Γöé   Γö£ΓöÇΓöÇ rag/
Γöé   Γöé   Γö£ΓöÇΓöÇ embedder.py      # Ollama embedding service
Γöé   Γöé   Γö£ΓöÇΓöÇ hybrid_search.py # Vector + FTS + RRF
Γöé   Γöé   ΓööΓöÇΓöÇ reranker.py      # Result reranking
Γöé   Γö£ΓöÇΓöÇ tools/
Γöé   Γöé   Γö£ΓöÇΓöÇ ingest_tools.py  # MCP ingest tools
Γöé   Γöé   Γö£ΓöÇΓöÇ search_tools.py  # MCP search tools
Γöé   Γöé   Γö£ΓöÇΓöÇ graph_tools.py   # MCP graph tools
Γöé   Γöé   Γö£ΓöÇΓöÇ database_tools.py # MCP SQL + tag tools
Γöé   Γöé   Γö£ΓöÇΓöÇ sql_tools.py     # SQL query tools
Γöé   Γöé   ΓööΓöÇΓöÇ version_tools.py # Versioning tools
Γöé   ΓööΓöÇΓöÇ utils/
Γöé       ΓööΓöÇΓöÇ models.py        # Pydantic data models
Γö£ΓöÇΓöÇ tests/
Γöé   Γö£ΓöÇΓöÇ test_chunking.py
Γöé   Γö£ΓöÇΓöÇ test_rag.py
Γöé   ΓööΓöÇΓöÇ test_integration.py
ΓööΓöÇΓöÇ README.md
```

### Running tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# With coverage
pytest --cov=src --cov-report=term-missing
```

### Contributing

PRs and issues welcome. The project follows a few conventions:

- Python 3.11+ only. No type: ignore. No `Any` where a real type works.
- 250-line soft limit on source files. If a module grows past it, split it.
- TDD for new features. Tests go in `tests/` mirroring the `src/` structure.
- The abstract `GraphStore` interface is the pattern for swappable backends. Follow it for new storage layers.

---

## License

MIT. See [LICENSE](LICENSE) for details.
