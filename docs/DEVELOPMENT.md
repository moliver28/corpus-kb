# Development Guide

This page is for contributors and anyone who wants to understand how Corpus-KB is built.

---

## Project structure

```
corpus-kb/
├── config.yaml                 # Runtime configuration
├── config/ontology.yaml        # Entity/relation type vocabulary
├── pyproject.toml              # Package metadata and dependencies
├── pyrightconfig.json          # Pyright type-checker config (basic mode)
├── migrations/                 # Idempotent SQL migrations
│   ├── 001_corpus_schema.sql
│   ├── 002_corpus_rag_schema.sql
│   └── 003_enable_extensions.sql
├── src/
│   ├── server.py               # Legacy FastMCP entrypoint
│   ├── server_wiring.py        # New async startup: HTTP, socket, MCP, projections
│   ├── config.py               # Config loader with env overrides
│   ├── ontology.py             # Ontology loader and Pydantic model
│   ├── partitioning.py         # Unstructured partition wrapper
│   ├── api/
│   │   ├── http.py             # Starlette REST routes
│   │   └── socket.py           # JSON-RPC socket server
│   ├── domain/
│   │   ├── aggregates.py       # Eventsourcing aggregates
│   │   ├── application.py      # Eventsourcing app factory
│   │   └── models.py           # Pydantic command/query models
│   ├── handlers/
│   │   ├── command_handler.py  # Ingest, entity, relation commands
│   │   ├── query_handler.py    # Search, SQL, list queries
│   │   ├── graph_handler.py    # Graph traversal
│   │   ├── tag_handler.py      # Tags and metadata
│   │   ├── versioning_handler.py  # Versions and stats
│   │   └── idempotency.py      # Command deduplication
│   ├── projections/
│   │   ├── embed_projection.py # Async chunk embedding
│   │   ├── documents_projection.py
│   │   ├── checkpoint.py
│   │   └── dlq.py              # Dead-letter queue
│   ├── chunking/               # File detection and chunkers (tree-sitter, markdown, text)
│   ├── rag/                    # Embedder, hybrid search, reranker
│   ├── storage/                # PostgresGraphStore, LlamaIndexPostgresBackend, RagBackend protocol
│   ├── extraction/             # Entity/relation extractors (regex, langextract, pgml)
│   ├── tools/                  # MCP tool modules (ingest_common, ingest_tools)
│   └── utils/                  # Shared models
├── tests/                      # Pytest suite
├── scripts/
│   ├── install.py              # Full-stack installer (doctor + install --apply)
│   ├── migrate.py              # Idempotent SQL migration runner
│   ├── validate_configs.py     # MCP config validator (CI gate)
│   └── demo.py                 # Smoke test
├── docs/INGESTION.md           # Full pipeline documentation
└── mcp-configs/                # Editor MCP configs
```

---

## Architecture deep dive

### Event sourcing

The system stores state as a sequence of events rather than updating rows directly. Domain aggregates (`Document`, `Entity`, `Relation`) apply events and are persisted through the eventsourcing library.

Key concepts:

- **Commands** validate input and save aggregates.
- **Events** are immutable facts.
- **Projections** read the event stream and update Postgres tables.
- **Checkpoints** track how far each projection has read.
- **DLQ** stores projection failures for retry or inspection.

### Projection lifecycle

1. A command saves a new event.
2. The eventsourcing library appends it to `event_store`.
3. Projections with catch-up subscriptions receive the event.
4. The projection updates Postgres and writes its checkpoint.
5. If the projection fails, the event lands in `projection_dlq`.

### RLS isolation

Every database connection runs `SET LOCAL app.current_tenant_id = '<uuid>'` before querying. Postgres RLS policies filter rows by that setting. This keeps tenant data isolated without changing SQL queries.

---

## Setup a development environment

```bash
git clone https://github.com/moliver28/corpus-kb.git
cd corpus-kb
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Install Postgres 17 with pgvector and AGE, create the database, and run migrations (`python scripts/migrate.py`). See [INSTALL.md](INSTALL.md) for platform details. Or use the installer: `python scripts/install.py install --apply`.

---

## Running tests

```bash
# All tests
pytest

# Ingest tests only
pytest tests/test_ingest.py -v --tb=short

# With coverage
pytest --cov=src --cov-report=term-missing
```

Tests that need Ollama are marked with `@pytest.mark.requires_ollama`. Tests that need Unstructured hi_res mode are skipped on Windows because detectron2 is unavailable.

---

## Conventions

- Python 3.11+ only.
- Use `from __future__ import annotations` in every file.
- Prefer type hints. Avoid `Any` where a real type works.
- No `type: ignore` comments.
- 250-line soft limit on source files; split modules that grow past it.
- TDD for new features.
- Tests mirror the `src/` structure under `tests/`.

---

## PR workflow

1. Create a branch from `master`. Allowed prefixes: `feature/`, `bugfix/`, `hotfix/`, `docs/`, `chore/`, `release/`.
2. Make atomic commits with conventional commit messages. The first line must start with a type prefix such as `feat:`, `fix:`, `chore:`, `docs:`.
3. Push and open a pull request.
4. Wait for CI: lint, type-check, validate-configs, and tests on three OSes.
5. Merge only after all checks pass.

---

## Governance checks

The repository runs additional workflows:

- **Branch validation** - enforces naming conventions.
- **Commit validation** - checks commit message format.
- **Agent governance** - enforces rules for automated contributors.

See `.github/workflows/` for details.

---

## Next steps

- [Install guide](INSTALL.md)
- [Features overview](FEATURES.md)
- [Admin guide](ADMIN.md)
- [API reference](API.md)
- [FAQ](FAQ.md)
