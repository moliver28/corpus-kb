# Corpus-KB Agent Instructions

## Quick Reference

```bash
# Install (editable)
pip install -e .
pip install -e ".[dev]"

# Lint
ruff check src/ tests/
ruff format --check src/ tests/

# Type-check (pyright — NOT mypy)
pyright src/

# Validate MCP configs before pushing
python scripts/validate_configs.py

# Tests (Ollama must be running with nomic-embed-text pulled)
python -m pytest tests/ -v --tb=short

# Coverage
pytest --cov=src --cov-report=term-missing
```

## Toolchain (non-standard choices)

- **Linter/formatter**: `ruff` (not flake8, not black). Commands above.
- **Type checker**: `pyright` (not mypy). Strict mode on `src/`.
- **Build backend**: setuptools (not hatch/poetry). Packages found in `src/`.
- **Entry point**: `corpus-kb` maps to `server:main` — `src/server.py::main()`.

## CI Gate Order

CI runs these in order: `lint → type-check → validate-configs → test`. Tests depend on validate-configs passing. All three OS (ubuntu, windows, macos) run tests. Ollama is installed and the model pulled before tests run — CI will fail if `nomic-embed-text` is not available.

The CI test command specifically runs `python -m pytest tests/test_ingest.py -v --tb=short --durations=10`. If adding new test files, update the CI workflow.

## Repo Structure & Ownership

- `src/server.py` — FastMCP entrypoint and CLI (`--transport stdio|sse`, `--port`, `--config`)
- `src/chunking/` — File type detection → chunker dispatch. Strategy pattern: CodeChunker (tree-sitter AST), MarkdownChunker (heading boundaries), TextChunker (semantic gap detection).
- `src/storage/` — Three backends: LanceDB (vectors + versioning), DuckDB (relational SQL), GraphStore (abstract ABC → SQLite implementation).
- `src/rag/` — OllamaEmbedder (SHA256 cache, zero-vector fallback on failure), HybridSearcher (vector + FTS + RRF fusion), Reranker (identity pass-through).
- `src/tools/` — 34 MCP tools across 6 modules. Each exports `register_tools(mcp, ...)`.
- `src/graph/` — Entity extractor + BFS traversal. Note: entity extraction from ingested code is **currently broken** (see Known Issues).
- `src/config.py` — Proper config loader with env var overrides. Prefer this over the inline `load_config()` in `server.py` for new code.
- `config.yaml` at repo root is the primary config. `corpus-kb/config.yaml` and `~/.corpus-kb/config.yaml` are fallback paths.
- `scripts/validate_configs.py` — Validates all MCP config JSON files for format correctness and cross-config consistency. Must pass in CI.
- `mcp-configs/` — Per-editor MCP config files. `opencode.json` uses `"mcp"` key (new format), `claude-code.json` and `cursor.json` use `"mcpServers"` (legacy). **Do not** use `mcpServers` in OpenCode format.

## Data Model Conventions

- Internal models are `@dataclass`, not `pydantic.BaseModel` (see `src/utils/models.py`).
- `Chunk.to_lance()` serializes dict/list fields to JSON strings for LanceDB storage. `from_lance()` deserializes them back.
- Every file uses `from __future__ import annotations`.
- **No `type: ignore`**. **No `Any` where a real type works.** Python 3.11+ only.
- 250-line soft limit on source files. If a module grows past it, split it.

## Test Conventions

- TDD for new features. Tests go in `tests/` mirroring `src/` structure.
- Integration tests mock Ollama (zero-vector fallback). Tests degrade gracefully when Ollama is unavailable.
- Tests need `pip install -e .` (editable install) to resolve imports without `sys.path` hacks. Some older tests use `sys.path.insert(0, ...)` as a fallback — prefer the editable install.
- `pytest-asyncio` is a dev dependency but async fixtures are not in wide use yet.
- Config validation tests live in `tests/test_validate_configs.py` and import from `scripts/validate_configs.py` directly.

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

The abstract `GraphStore` class in `src/storage/graph_store.py` is the contract for all graph backends. To add a new backend:

1. Subclass `GraphStore` and implement all `@abstractmethod` methods.
2. Add a factory branch in `create_graph_store()`.
3. **Do not** modify MCP graph tools — they call the interface, not the implementation.

## Branch & Commit Conventions

- Branches: `feature/*`, `bugfix/*`, `hotfix/*` (gitflow). Protected branches: `main`, `master`, `develop`.
- Commit messages: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, etc.), must start with a capital letter, 10–500 chars.

## Known Issues (check open GitHub issues before fixing)

- **#13 (HIGH BUG)**: Knowledge graph stays empty after ingest. Entity extraction pipeline is broken — entities/relations are not being populated despite `extract_entities: true`.
- **#15/#16 (HIGH/MEDIUM BUG)**: `setup.sh` does not produce a working install on clean macOS and has several minor defects.
- **#17 (HIGH FEATURE)**: Zero-data-loss shutdown/restart with transactional ingest.

## Repository Map

See `codemap.md` for full architecture. Per-folder codemaps at:
`src/codemap.md`, `src/chunking/codemap.md`, `src/storage/codemap.md`, `src/rag/codemap.md`, `src/tools/codemap.md`, `src/utils/codemap.md`, `scripts/codemap.md`.

Default embedding model: `nomic-embed-text` (768d). Upgradeable to `qwen3-embedding:8b-q8_0` (4096d).
