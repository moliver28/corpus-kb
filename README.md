# Corpus-KB

[![CI](https://github.com/moliver28/corpus-kb/actions/workflows/ci.yml/badge.svg)](https://github.com/moliver28/corpus-kb/actions/workflows/ci.yml)

**Local RAG system for AI code editors. Ingest your codebase. Ask questions. Get answers. No cloud.**

Corpus-KB is a private knowledge base for AI coding assistants. It reads your code, documentation, and notes, then answers questions grounded in your actual files. Everything runs on your machine: Postgres stores the data, Ollama generates embeddings, and a local server exposes the whole thing through MCP tools, HTTP endpoints, and a JSON-RPC socket.

---

## What you get

- **Hybrid search** that blends vector similarity, full-text search, and rank fusion
- **Knowledge graph** with entities, relations, and BFS traversal
- **Ontology-aware extraction** with configurable entity/relation types and pluggable backends (regex, LangExtract, PostgresML)
- **LlamaIndex RAG backend** with PGVectorStore and Ollama for local vector search
- **Event sourcing** for audit trails and time-travel queries
- **Multi-tenant Postgres** with row-level security on every table
- **Full-stack installer** with hardware detection, profile-based recommendations, and guided setup
- **MCP, HTTP, and socket APIs** so any editor or script can talk to it

---

## Architecture

```mermaid
graph TB
    Editor[AI Code Editor] -->|MCP stdio| MCP[FastMCP Server]
    Editor -->|HTTP :8010| HTTP[Starlette API]
    Editor -->|JSON-RPC socket| Socket[Unix socket / named pipe]
    MCP --> Handlers[Command / Query Handlers]
    HTTP --> Handlers
    Socket --> Handlers
    Handlers --> Domain[Domain Layer<br/>Aggregates + Events]
    Domain --> ES[Event Store<br/>append-only]
    ES --> Projections[Async Projections]
    Projections --> PG[PostgreSQL 17]
    PG --> VEC[pgvector<br/>vector search]
    PG --> FTS[Postgres FTS<br/>to_tsvector]
    PG --> AGE[Apache AGE<br/>Cypher graphs]
    PG --> RLS[RLS on 12 tables]
    Projections --> Ollama[Ollama<br/>embedding service]
```

1. **Ingest** a file, directory, or raw text.
2. The pipeline partitions it into chunks, embeds each chunk through Ollama, extracts entities and relations, and stores the result.
3. Commands append events to the event store; async projections write the read models into Postgres.
4. Your editor queries the read models through search, SQL, or graph traversal.

---

## Event sourcing flow

```mermaid
sequenceDiagram
    participant C as Client
    participant H as CommandHandler
    participant A as Document Aggregate
    participant ES as Event Store
    participant P as Projection
    participant DB as Postgres Tables

    C->>H: ingest_file(file_path)
    H->>A: create Document
    A->>A: apply Ingested event
    A->>A: apply ChunksAdded event
    H->>ES: app.save(aggregate)
    ES-->>H: event_id, version
    H-->>C: {status: "success"}
    ES->>P: subscribe(ChunksAdded)
    P->>P: embed chunks via Ollama
    P->>DB: INSERT chunks, chunks_vectors
    P->>DB: UPDATE projection_checkpoints
```

Events are the source of truth. Projections are derived and can be rebuilt by replaying the event log. Vectors live in the `chunks_vectors` table and are treated as derived data, not event payload.

---

## Quick start

From zero to a working system in about ten minutes:

```bash
# 1. Install Postgres 17 with pgvector, then create a database
#    See docs/INSTALL.md for platform-specific steps.

# 2. Clone the repo
git clone https://github.com/moliver28/corpus-kb.git
cd corpus-kb

# 3. Install the package
pip install -e ".[dev]"

# 4. Run the installer (detects hardware, creates DB, runs migrations, pulls models)
cd corpus-kb
python scripts/install.py doctor     # read-only diagnostics
python scripts/install.py install --apply   # guided setup with confirmations

# Or load the schema manually:
#   psql -d postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb \
#     -f corpus-kb/migrations/001_corpus_schema.sql

# 5. Pull the embedding model
ollama pull nomic-embed-text

# 6. Start the server
export CORPUS_KB_DATABASE_URL=postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
python -m corpus-kb.src.server_wiring --transport http --port 8010
```

In another terminal:

```bash
# Ingest a file
curl -X POST http://localhost:8010/api/ingest/file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "corpus-kb/src/server_wiring.py"}'

# Search
curl -X POST http://localhost:8010/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "how does startup work"}'
```

See [docs/INSTALL.md](docs/INSTALL.md) for the full setup guide.

---

## Documentation

| Page | What it covers |
|------|----------------|
| [Install](docs/INSTALL.md) | Full setup from scratch: Postgres, Python, Ollama, schema, first query |
| [Features](docs/FEATURES.md) | Ingest, search, graph, tags, metadata, versioning, embedding models, LlamaIndex RAG |
| [Admin](docs/ADMIN.md) | Configuration, schema, multi-tenancy, backups, monitoring, CI/CD |
| [API](docs/API.md) | HTTP routes, request bodies, curl examples, MCP tool reference |
| [Development](docs/DEVELOPMENT.md) | Architecture deep dive, testing, PR workflow, conventions |
| [CI](docs/ci.md) | MCP config validation, fail-fast pipeline behavior |
| [FAQ](docs/FAQ.md) | Common questions |
| [Ingestion](corpus-kb/docs/INGESTION.md) | Full pipeline documentation: partition, chunk, embed, extract, store |

---

## Editor integration

Corpus-KB speaks MCP over stdio, so any MCP-compatible editor can connect:

- OpenCode
- Claude Code
- Cursor
- VS Code with Cline
- Any other MCP client

Config files live in `mcp-configs/`. The setup scripts rewrite them to point at your virtual environment.

---

## License

MIT License. See `pyproject.toml` for the full text.