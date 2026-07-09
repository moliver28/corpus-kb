# Corpus-KB Agent Instructions

## Repository Layout

The git repo root is `F:/Documents/OpenCode/Corpus/`. All active code is in `corpus-kb/src/`.

- **`corpus-kb/src/`** — active development tree. All new code goes here.
- **`src/`** (repo root) — legacy code from master. DO NOT edit, lint, or test this tree.

All commands below assume you are in the `corpus-kb/` subdirectory unless noted otherwise.

## Quick Reference

```bash
# All commands run from corpus-kb/ subdirectory:

# Install (editable, with Postgres + dev deps)
pip install -e .
pip install -e ".[postgres,dev]"

# Lint
ruff check src/ tests/
ruff format --check src/ tests/

# Type-check (pyright - NOT mypy)
pyright src/

# Validate MCP configs before pushing
python scripts/validate_configs.py

# Tests (Ollama must be running with nomic-embed-text pulled)
python -m pytest tests/ -v --tb=short --durations=10

# Start the server (HTTP + socket + projections)
python -m src.server_wiring --transport http --port 8010

# Start in MCP stdio mode (for editor agents)
python -m src.server_wiring --transport stdio
```

## Architecture

Corpus-KB uses **event sourcing** with **PostgreSQL 17** as the sole database:

- **PostgreSQL 17.2** (port 5433) with **pgvector 0.8.0** (vector search) and **Apache AGE 1.5.0** (Cypher graph queries)
- **eventsourcing library** (pip package >=9.0) for aggregate lifecycle (versioning, snapshots, event dispatch)
- **asyncpg** as the sole database driver (no SQLAlchemy, no psycopg2)
- **12 Postgres tables** with Row Level Security (RLS) on all tables
- **24 HTTP routes** (Starlette) + JSON-RPC socket adapter + MCP stdio
- **Async projections**: EmbedChunksProjection (pgvector), DocumentsProjection, checkpoint tracking, DLQ
- **Configurable embedding**: nomic-embed-text (768d) or qwen3-embedding:8b (4096d) via config

### Event Sourcing Flow

```
Command -> Aggregate (@event) -> Event Store (append-only) -> Projections (async) -> Read Models
```

1. Command handler creates aggregate, calls method (fires event)
2. eventsourcing library persists event to event_store table (immutable)
3. Async projections subscribe to events and update read models
4. Query handlers read from projection tables (documents, chunks, chunks_vectors, entities, relations)

## Toolchain

- **Linter/formatter**: `ruff` (not flake8, not black)
- **Type checker**: `pyright` (not mypy). Basic mode (see `corpus-kb/pyrightconfig.json`)
- **Build backend**: setuptools. Packages found in `src/`
- **Entry point**: `python -m src.server_wiring` with `--transport` (stdio|http|sse) and `--port` CLI args
- **Database**: PostgreSQL 17+ with pgvector + Apache AGE extensions

## CI Pipeline

**Workflow file**: `.github/workflows/ci.yml` (at repo root)

**Gate order**: `lint -> type-check -> validate-configs -> test (3 OSes)`

| Job | Runner | Key Details |
|-----|--------|-------------|
| lint | ubuntu-latest | `ruff check corpus-kb/src/ corpus-kb/tests/` |
| type-check | ubuntu-latest | `pyright src/` from `corpus-kb/` dir |
| validate-configs | ubuntu-latest | `python corpus-kb/scripts/validate_configs.py` |
| test | ubuntu/windows/macos | Ollama + `pytest tests/` from `corpus-kb/` dir |

## Repo Structure

All active code is under `corpus-kb/`:

- `corpus-kb/src/domain/` — Event sourcing domain layer
  - `aggregates.py` — Document, Entity, Relation aggregates (eventsourcing @event decorator)
  - `application.py` — CorpusApplication (eventsourcing Application + Postgres backend)
  - `models.py` — Pydantic v2 command/query/result models
- `corpus-kb/src/handlers/` — Command and query dispatch
  - `command_handler.py` — Wraps existing ingest_common pipeline, fires eventsourcing events
  - `query_handler.py` — asyncpg + pgvector hybrid search with RRF fusion
  - `graph_handler.py` — Graph search, BFS traversal, entity relations (recursive CTE + AGE Cypher)
  - `tag_handler.py` — Tags + metadata key-value store
  - `versioning_handler.py` — Versioning, stats, table listing via event store
  - `idempotency.py` — Command deduplication via idempotency_keys table
  - `error_handling.py` — Timeout, retry, structured error responses
- `corpus-kb/src/projections/` — Async event subscribers
  - `embed_projection.py` — EmbedChunksProjection: ChunksAdded -> Ollama embed -> pgvector INSERT
  - `checkpoint.py` — Projection checkpoint tracking for crash recovery
  - `dlq.py` — Dead-letter queue for failed projections
  - `documents_projection.py` — Documents, chunks, entities, relations projections
- `corpus-kb/src/api/` — Protocol adapters
  - `http.py` — Starlette REST API (24 routes)
  - `socket.py` — JSON-RPC 2.0 over Unix socket / Windows named pipe
- `corpus-kb/src/storage/` — Database schema
  - `schema.sql` — Postgres DDL (12 tables, RLS on all, pgvector vector(4096), ivfflat index)
  - `lance_store.py` — Legacy LanceDB store (for migration only)
- `corpus-kb/src/server_wiring.py` — Server startup (asyncpg pool + eventsourcing app + projections + HTTP + socket)
- `corpus-kb/src/extraction/` — Pluggable ontology extractor
  - `protocol.py` — Extractor Protocol (interface)
  - `regex_backend.py` — Zero-dependency regex extractor (default)
  - `langextract_backend.py` — LLM-based extractor with fixture support
  - `bert_backend.py` — Minimal spaCy NER (en_core_web_sm, falls back to regex)
- `corpus-kb/src/tools/ingest_common.py` — Pipeline orchestrator (partition -> chunk -> embed -> extract -> store)
- `corpus-kb/src/ontology.py` — Ontology loader and Pydantic model
- `corpus-kb/src/partitioning.py` — Unstructured partition wrapper
- `corpus-kb/src/config.py` — Config loader with defaults, deep_update, and env var overrides
- `corpus-kb/config.yaml` — Primary config (database.connection_string, embedding.model, etc.)
- `corpus-kb/scripts/migrate_to_postgres.py` — LanceDB -> Postgres direct cutover migration
- `corpus-kb/scripts/validate_migration.py` — Migration validation script
- `corpus-kb/docs/W0_POSTGRES_SETUP.md` — Postgres + pgvector + AGE setup guide

## Import Convention

All modules use relative imports (e.g., `from domain.aggregates import Document`, `from handlers.command_handler import get_command_handler`). No `from src.xxx` imports remain (issue #29 resolved).

## Data Model Conventions

- Aggregates use `@dataclass` with the eventsourcing library's `@event` decorator
- Command/query models use Pydantic v2 `BaseModel` (see `src/domain/models.py`)
- Postgres schema uses `vector(4096)` type (pgvector) for embeddings
- Every file uses `from __future__ import annotations`
- **No `type: ignore`**. **No `Any` where a real type works.** Python 3.11+ only.
- 250-line soft limit on source files. If a module grows past it, split it.

## Test Conventions

- TDD for new features. Tests go in `corpus-kb/tests/` mirroring `src/` structure.
- Integration tests mock Ollama (zero-vector fallback). Tests degrade gracefully when Ollama is unavailable.
- Tests need `pip install -e corpus-kb/.` (editable install) to resolve imports.
- `pytest-asyncio` is a dev dependency.
- E2E integration tests in `tests/test_integration_e2e.py` (10 test scenarios, skip without Postgres)
- LangExtract fixtures are SHA256-keyed JSONL files in `tests/fixtures/langextract_recorded/`

## Environment Variable Overrides

These override `config.yaml` values at runtime. Use `src/config.py`'s `load_config()` to get them auto-merged (priority: env var > config.yaml):

| Variable | Config Key |
|---|---|
| `CORPUS_KB_DATABASE_URL` | `database.connection_string` |
| `CORPUS_KB_EMBEDDING_MODEL` | `embedding.model` |
| `CORPUS_KB_EMBEDDING_DIMENSIONS` | `embedding.dimensions` |
| `CORPUS_KB_TRANSPORT` | `server.transport` |
| `CORPUS_KB_PORT` | `server.port` |

## Postgres Setup

PostgreSQL 17 with pgvector and Apache AGE is required. See `corpus-kb/docs/W0_POSTGRES_SETUP.md` for setup instructions.

Connection string format: `postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb`

Extensions:
- `CREATE EXTENSION IF NOT EXISTS vector;` (pgvector)
- `CREATE EXTENSION IF NOT EXISTS age;` (Apache AGE)
- `CREATE EXTENSION IF NOT EXISTS "uuid-ossp";` (UUID generation)

Schema: `psql -U corpus_user -d corpus_kb -f src/storage/schema.sql`

## Branch & Commit Conventions

- Branches: `feature/*`, `bugfix/*`, `hotfix/*` (gitflow). Protected branches: `main`, `master`, `develop`.
- Commit messages: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, etc.), must start with a capital letter, 10-500 chars.
- **Commit message validator** checks each line individually - use single-line commit messages or ensure every body line starts with a conventional commit prefix.

## Known Issues (check open GitHub issues before fixing)

- **#15/#16 (HIGH/MEDIUM BUG)**: `setup.sh` does not produce a working install on clean macOS and has several minor defects.
- **#31 (MEDIUM FEATURE)**: Upgrade NER extraction to BERT/transformer models (follow-up from minimal spaCy NER).
- **#2 (MEDIUM TASK)**: PyPI publish (corpus-kb).
- **#6 (LOW FEATURE)**: Graph Level 3b: LatticeDB backend (future).

## Repository Map

Default embedding model: configurable via `config.yaml` (`embedding.model`). Supports `nomic-embed-text` (768d) and `qwen3-embedding:8b-q8_0` (4096d).
