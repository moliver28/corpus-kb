# Corpus-KB Agent Instructions

## Repository Layout (CRITICAL — read first)

The git repo root is `F:/Documents/OpenCode/Corpus/`, **not** `corpus-kb/`.
The repo contains **two** `src/` directories:

1. **`src/`** (repo root) — legacy code from master. **DO NOT edit, lint, or test this tree.** It has pre-existing ruff/pyright errors and stale imports.
2. **`corpus-kb/src/`** — active development tree. **All new code goes here.** All CI paths point at this subdirectory.

All commands below assume you are in the `corpus-kb/` subdirectory unless noted otherwise.

## Quick Reference

```bash
# All commands run from corpus-kb/ subdirectory:

# Install (editable)
pip install -e .
pip install -e ".[dev]"

# Lint
ruff check src/ tests/
ruff format --check src/ tests/

# Type-check (pyright — NOT mypy)
# Uses corpus-kb/pyrightconfig.json (basic mode, not strict)
pyright src/

# Validate MCP configs before pushing
python scripts/validate_configs.py

# Tests (Ollama must be running with nomic-embed-text pulled)
python -m pytest tests/test_ingest.py -v --tb=short --durations=10

# Coverage
pytest --cov=src --cov-report=term-missing
```

## Toolchain (non-standard choices)

- **Linter/formatter**: `ruff` (not flake8, not black). Commands above.
- **Type checker**: `pyright` (not mypy). Basic mode with specific warnings downgraded (see `corpus-kb/pyrightconfig.json`). Previously strict mode but 147 pre-existing type errors blocked CI; switched to basic in PR #28.
- **Build backend**: setuptools (not hatch/poetry). Packages found in `src/`.
- **Entry point**: `corpus-kb` maps to `server:main` — `src/server.py::main()`.

## CI Pipeline

**Workflow file**: `.github/workflows/ci.yml` (at repo root, NOT in `corpus-kb/`)

**Gate order**: `lint → type-check → validate-configs → test (3 OSes)`

| Job | Runner | Key Details |
|-----|--------|-------------|
| lint | ubuntu-latest | `ruff check corpus-kb/src/ corpus-kb/tests/` — fast, no deps |
| type-check | ubuntu-latest | `pyright src/` from `corpus-kb/` dir, project deps installed |
| validate-configs | ubuntu-latest | `python corpus-kb/scripts/validate_configs.py` |
| test | ubuntu/windows/macos | Ollama + `pytest tests/test_ingest.py` from `corpus-kb/` dir |

**Ollama install strategy per OS** (see ci.yml comments for full rationale):
- **Ubuntu**: `curl -fsSL https://ollama.com/install.sh | sh` (works reliably)
- **macOS**: `brew install ollama` (curl\|sh fails on macOS runners — exit code 1)
- **Windows**: `OllamaSetup.exe /S` with 120s timeout + `continue-on-error: true` (silent installer often hangs on GitHub Actions Windows runners; tests fall back to degraded mode with zero-vector embeddings)

**Windows-specific fixes** (documented in ci.yml comments):
- `python-magic-bin` installed to fix "Windows fatal exception: access violation" from the `magic` library (unstructured dependency needs libmagic DLL)
- `continue-on-error: true` on Ollama install/pull/verify steps so tests run in degraded mode when Ollama is unavailable
- `timeout-minutes: 10` on test step prevents hung Ollama connection attempts

**Critical: `working-directory: corpus-kb`** on the test and type-check steps. Without it, pytest/pyright run from the repo root where the legacy `src/` tree shadows the editable install's `corpus-kb/src/`, causing `ModuleNotFoundError`. See issue #29 for the follow-up to fix imports permanently.

**RCA document**: `.omo/plans/ci-rca.md` — root cause analysis of the dual-source-tree issue.

## Repo Structure & Ownership

All active code is under `corpus-kb/`:

- `corpus-kb/src/server.py` — FastMCP entrypoint and CLI (`--transport stdio|sse`, `--port`, `--config`)
- `corpus-kb/src/chunking/` — File type detection → chunker dispatch. Strategy pattern: CodeChunker (tree-sitter AST), MarkdownChunker (heading boundaries), TextChunker (semantic gap detection). Also `unstructured_chunker.py` for Unstructured element → Chunk mapping.
- `corpus-kb/src/storage/` — Single backend: `PostgresGraphStore` (asyncpg + RLS). Implements the `GraphStore` ABC. The old `LanceDBStore` and `SQLiteGraphStore` have been removed. Also includes `LlamaIndexPostgresBackend` (PGVectorStore + Ollama) and `RagBackend` Protocol for future backends.
- `corpus-kb/src/rag/` — OllamaEmbedder (SHA256 cache, zero-vector fallback on failure), PgmlEmbedder (PostgresML in-database embeddings), HybridSearcher (vector + FTS + RRF fusion), Reranker (identity pass-through). Also `FakeEmbedder` for CI/degraded mode.
- `corpus-kb/src/tools/` — MCP tools across 6 modules. `ingest_common.py` is the pipeline orchestrator (partition → chunk → embed → extract → store). `ingest_tools.py` is the thin MCP tool wrapper. All ingest functions are async and write directly to Postgres via asyncpg. `ingest_text` accepts an optional `source` parameter for custom source identifiers. `delete_document` removes a document and cascades to chunks/entities/relations.
- `corpus-kb/src/extraction/` — Pluggable ontology extractor: `protocol.py` (Extractor Protocol), `regex_backend.py` (zero-dep fallback), `langextract_backend.py` (LLM-based with fixture support), `pgml_backend.py` (PostgresML ONNX NER with regex fallback).
- `corpus-kb/src/ontology.py` — Ontology loader and Pydantic model (entity_types, relation_types with validation).
- `corpus-kb/src/partitioning.py` — Unstructured partition wrapper with typed ElementProxy.
- `corpus-kb/src/config.py` — Config loader with defaults, deep_update, and env var overrides.
- `corpus-kb/config.yaml` — Primary config. Also `config/ontology.yaml` for entity/relation type vocabulary.
- `corpus-kb/scripts/install.py` — Full-stack intelligent installer with `doctor` (read-only diagnostics) and `install --apply` (guided setup with per-step confirmation).
- `corpus-kb/scripts/migrate.py` — Idempotent SQL migration runner. Tracks applied migrations in `corpus.schema_migrations`.
- `corpus-kb/scripts/validate_configs.py` — Validates all MCP config JSON files. Must pass in CI.
- `corpus-kb/migrations/` — SQL migration files (`001_corpus_schema.sql`, `002_corpus_rag_schema.sql`, `003_enable_extensions.sql`).
- `corpus-kb/docs/INGESTION.md` — Full pipeline documentation (partition, chunk, embed, extract, store, error handling, fixture system).
- `mcp-configs/` — Per-editor MCP config files. `opencode.json` uses `"mcp"` key (new format), `claude-code.json` and `cursor.json` use `"mcpServers"` (legacy). **Do not** use `mcpServers` in OpenCode format.

## Import Convention (IMPORTANT)

All modules under `corpus-kb/src/` use **relative imports** (e.g., `from ..config import load_config`, `from .graph_store import PostgresGraphStore`). This was resolved in issue #29 (commit `fe3dc9e`).

**Tests** under `corpus-kb/tests/` use **absolute** `from src.xxx` imports because pytest top-level test modules cannot use relative imports. This is intentional and correct.

**5 remaining `from src.` imports in `src/`** (intentional — these are cross-package references that break when imported as top-level packages by callers using `from rag.embedder import ...`):
- `server_wiring.py` — `from src.rag.embedder import OllamaEmbedder`
- `handlers/query_handler.py` — `from src.rag.embedder import OllamaEmbedder`
- `projections/embed_projection.py` — `from src.rag.embedder import OllamaEmbedder`
- `extraction/__init__.py` — `from src.extraction.pgml_backend import PgmlExtractor`
- `extraction/pgml_backend.py` — `from src.extraction.regex_backend import RegexExtractor`

These were changed from relative (`from ..rag.embedder`) back to absolute (`from src.rag.embedder`) because the relative form caused `ImportError: attempted relative import beyond top-level package` when `rag` was imported as a top-level package.

## Data Model Conventions

- Internal models are `@dataclass`, not `pydantic.BaseModel` (see `src/utils/models.py`). Exception: `Ontology` in `src/ontology.py` uses Pydantic for validation.
- `Chunk.to_lance()` serializes dict/list fields to JSON strings for LanceDB storage. `from_lance()` deserializes them back.
- Every file uses `from __future__ import annotations`.
- **No `type: ignore`**. **No `Any` where a real type works.** Python 3.11+ only.
- 250-line soft limit on source files. If a module grows past it, split it.

## Test Conventions

- TDD for new features. Tests go in `corpus-kb/tests/` mirroring `src/` structure.
- Integration tests mock Ollama (zero-vector fallback via `OllamaEmbedder` catching `ConnectionError`). Tests degrade gracefully when Ollama is unavailable.
- Tests need `pip install -e corpus-kb/.` (editable install) to resolve imports without `sys.path` hacks.
- `pytest-asyncio` is a dev dependency. `asyncio_mode = "auto"` is set in `pyproject.toml`.
- Config validation tests live in `tests/test_validate_configs.py` and import from `scripts/validate_configs.py` directly.
- LangExtract fixtures are SHA256-keyed JSONL files in `tests/fixtures/langextract_recorded/` for deterministic CI runs without live LLM calls.
- `conftest.py` provides `pg_pool` fixture (asyncpg pool for Postgres tests) and `graph_store` fixture (PostgresGraphStore). Tests requiring Postgres are marked `@pytest.mark.asyncio`.

## Environment Variable Overrides

These override `config.yaml` values at runtime. Use `src/config.py`'s `load_config()` to get them auto-merged (priority: env var > CLI flag > config.yaml):

| Variable | Config Key |
|---|---|
| `CORPUS_KB_DATABASE_URL` | `database.connection_string` |
| `CORPUS_KB_EMBEDDING_MODEL` | `embedding.model` |
| `CORPUS_KB_EMBEDDING_DIMENSIONS` | `embedding.dimensions` |
| `CORPUS_KB_GRAPH_BACKEND` | `graph.backend` |
| `CORPUS_KB_TRANSPORT` | `server.transport` |
| `CORPUS_KB_PORT` | `server.port` |

## GraphStore Pattern (for new backends)

The abstract `GraphStore` class in `corpus-kb/src/storage/graph_store.py` is the contract for all graph backends. To add a new backend:

1. Subclass `GraphStore` and implement all `@abstractmethod` methods (all async).
2. The `PostgresGraphStore` implementation uses asyncpg with RLS and recursive CTE BFS.
3. When Apache AGE is available, set `graph.backend: age` in config to use openCypher `MATCH` instead of recursive CTEs.
4. **Do not** modify MCP graph tools — they call the interface, not the implementation.

The `PostgresGraphStore` implementation includes:
- Transactional writes via `@asynccontextmanager transaction()` — all graph writes (document, chunks, entities, relations) are atomic
- Provenance tables (documents, chunks) with FK enforcement (`PRAGMA foreign_keys=ON` per connection)
- Batch operations (`batch_add_entities`, `batch_add_relations`) with application-level validation
- Relation cap (`MAX_RELATIONS_PER_CHUNK = 10`) to prevent combinatorial explosion

## Branch & Commit Conventions

- Branches: `feature/*`, `bugfix/*`, `hotfix/*` (gitflow). Protected branches: `main`, `master`, `develop`.
- Commit messages: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, etc.), must start with a capital letter, 10–500 chars.
- **Commit message validator** (`.github/workflows/commit-validate.yml`) checks each line individually — use single-line commit messages or ensure every body line starts with a conventional commit prefix.

## Known Issues (check open GitHub issues before fixing)

- **#13 (HIGH BUG)**: ✅ Resolved — ontology pipeline cherry-picked and adapted to Postgres (commit `6d62175`). Entity/relation extraction now works via `src/extraction/` with regex, langextract, and pgml backends.
- **#15/#16 (HIGH/MEDIUM BUG)**: ✅ Resolved — `setup.sh` replaced by full-stack installer `scripts/install.py` (commit `dc17c25`). Use `corpus-kb doctor` and `corpus-kb install --apply`.
- **#17 (HIGH FEATURE)**: Zero-data-loss shutdown/restart with transactional ingest. Not yet implemented.
- **#29 (MEDIUM TASK)**: ✅ Mostly resolved — `corpus-kb/src/` converted to relative imports (commit `fe3dc9e`). 5 files remain using `from src.` due to top-level package import constraints (see Import Convention section above).
- **#31 (MEDIUM FEATURE)**: Upgrade NER extraction to BERT/transformer models. Not yet implemented.
- **#2 (MEDIUM TASK)**: PyPI publish. Not yet implemented.

## Repository Map

See `codemap.md` for full architecture. Per-folder codemaps at:
`src/codemap.md`, `src/chunking/codemap.md`, `src/storage/codemap.md`, `src/rag/codemap.md`, `src/tools/codemap.md`, `src/utils/codemap.md`, `scripts/codemap.md`.

Default embedding model: `nomic-embed-text` (768d). Upgradeable to `qwen3-embedding:8b-q8_0` (4096d). PostgresML (`pgml`) also supported for in-database embeddings.
