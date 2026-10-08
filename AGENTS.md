# Corpus-KB Agent Instructions

## Repository Layout (CRITICAL — read first)

These instructions apply at the repository root (the directory containing this file — the canonical checkout location is machine-specific).
Corpus-KB now uses a **single source tree**:

- **`src/corpus_kb/`** — active Python package. **All new code goes here.**
- **`tests/`** — root-level pytest suite.
- **`config.yaml`** — root runtime config.
- **`src/corpus_kb/config.yaml`** — packaged default config.
- **`src/corpus_kb/migrations/`** — idempotent SQL migrations.
- **`scripts/`** — root-level shims for backward compatibility.
- **`src/corpus_kb/_setup/`** — installer, migration runner, and config validator.

All commands below assume you are at the **repo root** unless noted otherwise.

## Quick Reference

```bash
# Install (editable)
pip install -e .
pip install -e ".[dev]"

# Lint
ruff check src/ tests/
ruff format --check src/ tests/

# Type-check (pyright — NOT mypy)
# Uses pyrightconfig.json in the repo root (basic mode, not strict)
pyright src/

# Validate MCP configs before pushing
python src/corpus_kb/_setup/validate_configs.py

# Tests (Ollama must be running with nomic-embed-text pulled)
python -m pytest tests/ -v --tb=short --durations=10

# Coverage
pytest --cov=corpus_kb --cov-report=term-missing
```

## CLI Commands

The `corpus-kb` entry point now routes through `corpus_kb.cli:app`:

```bash
# One-line setup: docker compose, database, migrations, models
corpus-kb setup --dry-run   # preview
corpus-kb setup             # apply

# Read-only diagnostics
corpus-kb doctor

# Start the server
corpus-kb start --transport http --port 8010
```

The old root `scripts/install.py` is a deprecation shim. It warns and delegates to `corpus_kb._setup.install`.

## Toolchain (non-standard choices)

- **Linter/formatter**: `ruff` (not flake8, not black). Commands above.
- **Type checker**: `pyright` (not mypy). Basic mode with specific warnings downgraded (see `pyrightconfig.json`). Previously strict mode but 147 pre-existing type errors blocked CI; switched to basic in PR #28.
- **Build backend**: setuptools (not hatch/poetry). Packages found in `src/`.
- **Entry point**: `corpus-kb` maps to `corpus_kb.cli:app`.

## CI Pipeline

**Workflow file**: `.github/workflows/ci.yml` (at repo root)

**Gate order**: `lint → type-check → validate-configs → test (3 OSes)`

| Job | Runner | Key Details |
|-----|--------|-------------|
| lint | ubuntu-latest | `ruff check src/ tests/` — fast, no deps |
| type-check | ubuntu-latest | `pyright src/` from repo root, project deps installed |
| validate-configs | ubuntu-latest | `python src/corpus_kb/_setup/validate_configs.py` |
| test | ubuntu/windows/macos | Ollama + `python -m pytest tests/` from repo root |

**Ollama install strategy per OS** (see ci.yml comments for full rationale):
- **Ubuntu**: `curl -fsSL https://ollama.com/install.sh | sh` (works reliably)
- **macOS**: `brew install ollama` (curl\|sh fails on macOS runners — exit code 1)
- **Windows**: `OllamaSetup.exe /S` with 120s timeout + `continue-on-error: true` (silent installer often hangs on GitHub Actions Windows runners; tests fall back to degraded mode with zero-vector embeddings)

**Windows-specific fixes** (documented in ci.yml comments):
- `python-magic-bin` installed to fix "Windows fatal exception: access violation" from the `magic` library (unstructured dependency needs libmagic DLL)
- `continue-on-error: true` on Ollama install/pull/verify steps so tests run in degraded mode when Ollama is unavailable
- `timeout-minutes: 10` on test step prevents hung Ollama connection attempts

## Repo Structure & Ownership

All active code is under `src/corpus_kb/`:

- `src/corpus_kb/cli.py` — Typer CLI (`setup`, `doctor`, `start`).
- `src/corpus_kb/server_wiring.py` — Async startup: HTTP, socket, MCP, projections.
- `src/corpus_kb/config.py` — Config loader with env overrides and packaged default fallback.
- `src/corpus_kb/ontology.py` — Ontology loader and Pydantic model.
- `src/corpus_kb/partitioning.py` — Unstructured partition wrapper.
- `src/corpus_kb/chunking/` — File type detection → chunker dispatch. Strategy pattern: CodeChunker (tree-sitter AST), MarkdownChunker (heading boundaries), TextChunker (semantic gap detection).
- `src/corpus_kb/storage/` — `PostgresGraphStore` (asyncpg + RLS), `AgeGraphStore`, `LlamaIndexPostgresBackend`, and `RagBackend` Protocol.
- `src/corpus_kb/rag/` — `OllamaEmbedder` (SHA256 cache, zero-vector fallback), `PgmlEmbedder`, `HybridSearcher`, `Reranker`.
- `src/corpus_kb/tools/` — MCP tools. `ingest_common.py` is the pipeline orchestrator (partition → chunk → embed → extract → store). `ingest_tools.py` is the thin MCP tool wrapper.
- `src/corpus_kb/extraction/` — Pluggable ontology extractor: `protocol.py`, `regex_backend.py`, `langextract_backend.py`, `pgml_backend.py`, `bert_backend.py`.
- `src/corpus_kb/_setup/` — `install.py` (full-stack installer), `migrate.py`, `validate_configs.py`, `check_ci.py`.
- `src/corpus_kb/migrations/` — SQL migration files (`001_corpus_schema.sql` through `006_rrf_fusion.sql`).
- `src/corpus_kb/config.yaml` — Packaged default config.
- `config.yaml` — Root runtime config.
- `config/ontology.yaml` — Entity/relation type vocabulary.
- `mcp-configs/` — Per-editor MCP config files (`claude-code.json`, `cursor.json`, `codex.json`, all using the `"mcpServers"` legacy key). The OpenCode editor config is the root `opencode.json`, which uses the `"mcp"` key (new format). **Do not** use `mcpServers` in OpenCode format. These configs pre-stage the not-yet-implemented MCP stdio server; `validate_configs.py` checks their internal consistency.

## Import Convention (IMPORTANT)

All modules under `src/corpus_kb/` use **absolute package imports** (e.g., `from corpus_kb.config import load_config`, `from corpus_kb.storage.graph_store import PostgresGraphStore`).

**Tests** under `tests/` use absolute `from corpus_kb.xxx` imports because pytest top-level test modules cannot use relative imports. This is intentional and correct.

**No `from src.` imports remain.** The old dual-tree special cases were removed when the package was consolidated under `src/corpus_kb/`. Everything is imported as `corpus_kb.*`.

## Data Model Conventions

- Internal models are `@dataclass`, not `pydantic.BaseModel` (see `src/corpus_kb/utils/models.py`). Exception: `Ontology` in `src/corpus_kb/ontology.py` uses Pydantic for validation.
- Every file uses `from __future__ import annotations`.
- **No `type: ignore`.** **No `Any` where a real type works.** Python 3.11+ only.
- 250-line soft limit on source files. If a module grows past it, split it.

## Test Conventions

- TDD for new features. Tests go in `tests/` mirroring `src/corpus_kb/` structure.
- Integration tests mock Ollama (zero-vector fallback via `OllamaEmbedder` catching `ConnectionError`). Tests degrade gracefully when Ollama is unavailable.
- Tests need `pip install -e .` (editable install) to resolve imports without `sys.path` hacks.
- `pytest-asyncio` is a dev dependency. `asyncio_mode = "auto"` is set in `pyproject.toml`.
- Config validation tests live in `tests/test_validate_configs.py` and import from `src/corpus_kb/_setup.validate_configs` directly.
- LangExtract fixtures are SHA256-keyed JSONL files in `tests/fixtures/langextract_recorded/` for deterministic CI runs without live LLM calls.
- `conftest.py` provides `pg_pool` fixture (asyncpg pool for Postgres tests) and `graph_store` fixture. Tests requiring Postgres are marked `@pytest.mark.asyncio`.

## Environment Variable Overrides

These override `config.yaml` values at runtime. Use `corpus_kb.config.load_config()` to get them auto-merged (priority: env var > CLI flag > config.yaml):

| Variable | Config Key |
|---|---|
| `CORPUS_KB_DATABASE_URL` | `database.connection_string` |
| `CORPUS_KB_EMBEDDING_MODEL` | `embedding.model` |
| `CORPUS_KB_EMBEDDING_DIMENSIONS` | `embedding.dimensions` |
| `CORPUS_KB_GRAPH_BACKEND` | `graph.backend` |
| `CORPUS_KB_TRANSPORT` | `server.transport` |
| `CORPUS_KB_PORT` | `server.port` |

## GraphStore Pattern (for new backends)

The abstract `GraphStore` class in `src/corpus_kb/storage/graph_store.py` is the contract for all graph backends. To add a new backend:

1. Subclass `GraphStore` and implement all `@abstractmethod` methods (all async).
2. The `PostgresGraphStore` implementation uses asyncpg with RLS and recursive CTE BFS.
3. `AgeGraphStore` uses Apache AGE openCypher `MATCH` when `graph.backend: age` is set.
4. **Do not** modify MCP graph tools — they call the interface, not the implementation.

The `PostgresGraphStore` implementation includes:
- Transactional writes via `@asynccontextmanager transaction()` — all graph writes (document, chunks, entities, relations) are atomic
- Provenance tables (documents, chunks) with FK enforcement
- Batch operations (`batch_add_entities`, `batch_add_relations`) with application-level validation
- Relation cap (`MAX_RELATIONS_PER_CHUNK = 10`) to prevent combinatorial explosion

## Branch & Commit Conventions

- Branches: `feature/*`, `bugfix/*`, `hotfix/*` (gitflow). Protected branches: `main`, `master`, `develop`.
- Commit messages: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, etc.), must start with a capital letter, 10–500 chars.
- **Commit message validator** (`.github/workflows/commit-validate.yml`) checks each line individually — use single-line commit messages or ensure every body line starts with a conventional commit prefix.
- Safe prefixes: `feat|fix|docs|chore|test|refactor|perf`. Avoid `build:`, `ci:`, and `style:`.

## Known Issues (check open GitHub issues before fixing)

- **#13 (HIGH BUG)**: ✅ Resolved — ontology pipeline adapted to Postgres. Entity/relation extraction works via `src/corpus_kb/extraction/` with regex, langextract, pgml, and bert backends.
- **#15/#16 (HIGH/MEDIUM BUG)**: ✅ Resolved — Native bash installer retired in favor of the full-stack Python installer at `src/corpus_kb/_setup/install.py` and the `corpus-kb setup` CLI command (container-first default).
- **#17 (HIGH FEATURE)**: Zero-data-loss shutdown/restart with transactional ingest. Not yet implemented.
- **#29 (MEDIUM TASK)**: ✅ Resolved — single-tree layout completed. Package now lives at `src/corpus_kb/` with `corpus_kb.*` absolute imports.
- **#31 (MEDIUM FEATURE)**: Upgrade NER extraction to BERT/transformer models. Partially implemented via `src/corpus_kb/extraction/bert_backend.py`.
- **#2 (MEDIUM TASK)**: PyPI publish. Not yet implemented.

## Repository Map

There is no root `codemap.md` today. Per-folder codemaps under `src/corpus_kb/**/codemap.md` have been removed. Use the structure section above and `docs/DEVELOPMENT.md` for orientation.

Default embedding model: `nomic-embed-text` (768d). Upgradeable to `qwen3-embedding:8b-q8_0` (4096d). PostgresML (`pgml`) also supported for in-database embeddings.
