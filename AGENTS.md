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
- `corpus-kb/src/storage/` — Three backends: LanceDB (vectors via `lance_store.py` + `_lance_typed.py`), DuckDB (relational SQL), GraphStore (abstract ABC → SQLite implementation in `graph_store.py` with transaction support).
- `corpus-kb/src/rag/` — OllamaEmbedder (SHA256 cache, zero-vector fallback on failure), HybridSearcher (vector + FTS + RRF fusion), Reranker (identity pass-through). Also `FakeEmbedder` for CI/degraded mode.
- `corpus-kb/src/tools/` — MCP tools across 6 modules. `ingest_common.py` is the pipeline orchestrator (partition → chunk → embed → extract → store). `ingest_tools.py` is the thin MCP tool wrapper.
- `corpus-kb/src/extraction/` — Pluggable ontology extractor: `protocol.py` (Extractor Protocol), `regex_backend.py` (zero-dep fallback), `langextract_backend.py` (LLM-based with fixture support), `_langextract_types.py` (typed Protocol wrappers for langextract).
- `corpus-kb/src/ontology.py` — Ontology loader and Pydantic model (entity_types, relation_types with validation).
- `corpus-kb/src/partitioning.py` — Unstructured partition wrapper with typed ElementProxy.
- `corpus-kb/src/config.py` — Config loader with defaults, deep_update, and env var overrides.
- `corpus-kb/config.yaml` — Primary config. Also `config/ontology.yaml` for entity/relation type vocabulary.
- `corpus-kb/scripts/validate_configs.py` — Validates all MCP config JSON files. Must pass in CI.
- `corpus-kb/docs/INGESTION.md` — Full pipeline documentation (partition, chunk, embed, extract, store, error handling, fixture system).
- `mcp-configs/` — Per-editor MCP config files. `opencode.json` uses `"mcp"` key (new format), `claude-code.json` and `cursor.json` use `"mcpServers"` (legacy). **Do not** use `mcpServers` in OpenCode format.

## Import Convention (IMPORTANT)

All modules under `corpus-kb/src/` currently use absolute `from src.xxx` imports (e.g., `from src.config import load_config`). This works because the editable install adds `corpus-kb/src/` to `sys.path`, but it is fragile — running from the repo root causes the legacy `src/` tree to shadow the active one. **Issue #29** tracks converting these to relative imports.

**Already converted** (in PR #28):
- `storage/__init__.py` — `from .graph_store`, `from .lance_store`
- `storage/lance_store.py` — `from ._lance_typed`
- `tools/ingest_common.py` — `from storage.graph_store`, `from storage.lance_store`
- `tools/ingest_tools.py` — `from storage.graph_store`, `from .ingest_common`

**Still using `from src.xxx`** (to be converted in issue #29):
- `chunking/__init__.py`, `chunking/unstructured_chunker.py`
- `extraction/__init__.py`, `extraction/protocol.py`, `extraction/regex_backend.py`, `extraction/langextract_backend.py`, `extraction/_langextract_types.py`
- `rag/__init__.py`, `rag/embedder.py`
- `storage/graph_store.py`, `storage/lance_store.py`
- `tools/ingest_common.py` (partial — some imports still use `from src.`)
- `tests/conftest.py`, `tests/test_ingest.py`

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
- `pytest-asyncio` is a dev dependency but async fixtures are not in wide use yet.
- Config validation tests live in `tests/test_validate_configs.py` and import from `scripts/validate_configs.py` directly.
- LangExtract fixtures are SHA256-keyed JSONL files in `tests/fixtures/langextract_recorded/` for deterministic CI runs without live LLM calls.
- `conftest.py` provides `graph_store_tmp` fixture (temporary SQLiteGraphStore) and skips `requires_hi_res` tests on Windows (detectron2 unavailable).

## Environment Variable Overrides

These override `config.yaml` values at runtime. Use `src/config.py`'s `load_config()` to get them auto-merged (priority: env var > CLI flag > config.yaml):

| Variable | Config Key |
|---|---|
| `CORPUS_KB_STORAGE_PATH` | `storage.path` |
| `CORPUS_KB_EMBEDDING_MODEL` | `embedding.model` |
| `CORPUS_KB_EMBEDDING_DIMENSIONS` | `embedding.dimensions` |
| `CORPUS_KB_GRAPH_BACKEND` | `graph.backend` |
| `CORPUS_KB_GRAPH_PATH` | `graph.db_path` |
| `CORPUS_KB_TRANSPORT` | `server.transport` |
| `CORPUS_KB_PORT` | `server.port` |

## GraphStore Pattern (for new backends)

The abstract `GraphStore` class in `corpus-kb/src/storage/graph_store.py` is the contract for all graph backends. To add a new backend:

1. Subclass `GraphStore` and implement all `@abstractmethod` methods.
2. Add a factory branch in `create_graph_store()`.
3. **Do not** modify MCP graph tools — they call the interface, not the implementation.

The SQLiteGraphStore implementation includes:
- Transactional writes via `@contextmanager transaction()` — all graph writes (document, chunks, entities, relations) are atomic
- Provenance tables (documents, chunks) with FK enforcement (`PRAGMA foreign_keys=ON` per connection)
- Batch operations (`batch_add_entities`, `batch_add_relations`) with application-level validation
- Relation cap (`MAX_RELATIONS_PER_CHUNK = 10`) to prevent combinatorial explosion

## Branch & Commit Conventions

- Branches: `feature/*`, `bugfix/*`, `hotfix/*` (gitflow). Protected branches: `main`, `master`, `develop`.
- Commit messages: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, etc.), must start with a capital letter, 10–500 chars.
- **Commit message validator** (`.github/workflows/commit-validate.yml`) checks each line individually — use single-line commit messages or ensure every body line starts with a conventional commit prefix.

## Known Issues (check open GitHub issues before fixing)

- **#13 (HIGH BUG)**: Knowledge graph stays empty after ingest. Entity extraction pipeline is broken — entities/relations are not being populated despite `extract_entities: true`. (Partially addressed by PR #28's ontology pipeline, but legacy `src/graph/extractor.py` may still be broken.)
- **#15/#16 (HIGH/MEDIUM BUG)**: `setup.sh` does not produce a working install on clean macOS and has several minor defects.
- **#17 (HIGH FEATURE)**: Zero-data-loss shutdown/restart with transactional ingest.
- **#29 (MEDIUM TASK)**: Convert `from src.xxx` absolute imports to relative imports across `corpus-kb/src/` to eliminate the dual-source-tree shadowing issue.

## Repository Map

See `codemap.md` for full architecture. Per-folder codemaps at:
`src/codemap.md`, `src/chunking/codemap.md`, `src/storage/codemap.md`, `src/rag/codemap.md`, `src/tools/codemap.md`, `src/utils/codemap.md`, `scripts/codemap.md`.

Default embedding model: `nomic-embed-text` (768d). Upgradeable to `qwen3-embedding:8b-q8_0` (4096d).
