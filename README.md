# Corpus-KB

**Local RAG system for AI code editors. Ingest your codebase. Ask questions. Get answers. No cloud.**

Corpus-KB is a private knowledge base for AI coding assistants. It reads your code, documentation, and notes, then answers questions grounded in your actual files. Everything runs on your machine: Postgres stores the data, Ollama generates embeddings, and a local server exposes the whole thing through MCP tools, HTTP endpoints, and a JSON-RPC socket.

---

## What you get

- **Hybrid search** that blends vector similarity, full-text search, and rank fusion
- **Knowledge graph** with entities, relations, and BFS traversal
- **Ontology-aware extraction** with configurable entity/relation types and pluggable backends (regex, LangExtract, PostgresML, BERT)
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
2. The pipeline partitions it into chunks, embeds each chunk through Ollama or PostgresML, extracts entities and relations, and stores the result.
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

# 4. Run diagnostics and guided setup
corpus-kb doctor                    # read-only diagnostics
corpus-kb setup                     # guided setup with confirmations

# Or load the schema manually:
#   psql -d postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb \
#     -f src/corpus_kb/migrations/001_corpus_schema.sql

# 5. Pull the embedding model
ollama pull nomic-embed-text

# 6. Start the server
export CORPUS_KB_DATABASE_URL=postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
corpus-kb start --transport http --port 8010

# 7. New to qualitative coding? Run the narrated demo (~5 minutes).
#    The demo needs a research-grade embedder (>=1024 dims), so pull that
#    first - the base nomic-embed-text model (768 dims) makes the demo
#    abstain by design:
ollama pull qwen3-embedding:8b-q8_0
corpus-kb research demo
# then read docs/getting-started.md to run it on your own transcripts
```

In another terminal:

```bash
# Ingest a file
curl -X POST http://localhost:8010/api/ingest/file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "src/corpus_kb/server_wiring.py"}'

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
| [Getting started (research)](docs/getting-started.md) | Qualitative coding from zero: the demo, your first transcript, first codebook |
| [Research](docs/research.md) | Research command reference, config keys, embedding requirements |
| [Understanding your report](docs/understanding-your-report.md) | Plain-language guide to every governance-report section |
| [Research math](docs/research-math.md) | Every formula, its v5 source, and its calibration provenance |
| [CI](docs/ci.md) | MCP config validation, fail-fast pipeline behavior |
| [FAQ](docs/FAQ.md) | Common questions |
| [Ingestion](docs/INGESTION.md) | Full pipeline documentation: partition, chunk, embed, extract, store |
| [Harness distribution](docs/harness-distribution.md) | Packaging and distributing the agent harness pack |
| [Rollback drill](docs/ROLLBACK_DRILL.md) | Practiced procedure for backing out a bad deployment |

---

## Editor integration

Editors and scripts drive Corpus-KB through the **HTTP API** (any MCP-compatible editor can call it via an HTTP bridge) and the JSON-RPC socket:

- HTTP: `corpus-kb start --transport http --port 8010` — every ingest/search/graph/research surface has a route (see [docs/API.md](docs/API.md))
- JSON-RPC socket: same command, for local automation
- The research and coding surfaces are also first-class CLI commands (`corpus-kb research ...`, `corpus-kb coding ...`)

An MCP-over-stdio server is planned but **not implemented yet**: the config files in `mcp-configs/` are pre-staged for it, and `corpus-kb --transport stdio` exits with a clear message rather than pretending. Until the MCP server lands, point editors at the HTTP API.

---

## License

MIT License. See `pyproject.toml` for the full text.
