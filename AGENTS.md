# Corpus-KB Agent Instructions

## Repository Layout (read first)

Single source tree; **all new code goes in `src/corpus_kb/`**. Commands below run from the repo root.

- `src/corpus_kb/` — active Python package (packaged default config at `src/corpus_kb/config.yaml`)
- `tests/` — root-level pytest suite
- `config.yaml` — root runtime config
- `src/corpus_kb/migrations/` — idempotent SQL migrations
- `scripts/` — backward-compat shims
- `src/corpus_kb/_setup/` — installer, migration runner, config validator
- `ui_src/` — TypeScript SPA source (React 19 + `antd` 6, `pnpm`)
- `src/corpus_kb/ui_dist/` — built SPA assets served at `/app/`

## Quick Reference

```bash
pip install -e ".[dev]"                          # install (editable)
ruff check src/ tests/                           # lint (ruff — NOT flake8/black)
ruff format --check src/ tests/
pyright src/                                     # type-check (pyright — NOT mypy; basic mode per pyrightconfig.json)
python src/corpus_kb/_setup/validate_configs.py  # validate MCP configs pre-push
python scripts/schema_parity_gate.py             # schema parity, both migration sets (sqlglot, static; poe schema-parity)
python src/corpus_kb/_setup/validate_profiles.py # profile gate: schema + strategy/template/seeds cross-refs (poe validate-profiles)
python -m pytest tests/ -v --tb=short --durations=10   # tests (Ollama up, nomic-embed-text pulled)
pytest --cov=corpus_kb --cov-report=term-missing      # coverage
```

## CLI

`corpus-kb` → `corpus_kb.cli:app`:

```bash
corpus-kb setup --dry-run   # preview (docker compose, database, migrations, models)
corpus-kb setup             # apply
corpus-kb doctor            # read-only diagnostics
corpus-kb start --transport http --port 8010
```

Old `scripts/install.py` is a deprecation shim delegating to `corpus_kb._setup.install`.

## Toolchain

Build: setuptools (not hatch/poetry), packages in `src/`. Lint/type-check tools: see Quick Reference.

## CI (`.github/workflows/ci.yml`)

Gate order: lint (`ruff check src/ tests/`, no deps) → type-check (`pyright src/`, project deps installed) → validate-configs (command above) → test (ubuntu/windows/macos: Ollama + `python -m pytest tests/` from repo root).

Ollama install per OS (exact commands + rationale in ci.yml comments). Ubuntu: official install script (reliable). macOS: `brew install ollama` (install script fails on runners). Windows: `OllamaSetup.exe /S` + 120s timeout + `continue-on-error: true` (installer often hangs → degraded zero-vector mode).

Windows fixes: `python-magic-bin` (libmagic DLL for the `magic` module in unstructured; fixes access-violation crash). `continue-on-error` on Ollama install/pull/verify. `timeout-minutes: 30` on the ubuntu test leg, `40` on windows/macos (hung Ollama connects; PR #189 raised the ubuntu cap with suite-run rationale).

## Repo Structure & Ownership

All active code under `src/corpus_kb/`:

- `cli/` — Typer CLI package (`cli/__init__.py` holds the app; `cli_*.py` command modules).
- `server_wiring.py` — async startup: HTTP, socket, MCP, projections.
- `config.py` — config loader, env overrides, packaged-default fallback.
- `ontology.py` — ontology loader + Pydantic model.
- `partitioning.py` — unstructured partition wrapper.
- `chunking/` — shared chunk primitives (`unstructured_chunker.py`, `hierarchy*.py`); strategy chunking lives in `ingestion/strategies/`: `code.py` + `ast_chunks.py` (tree-sitter AST behind the optional `[code]` extra), `markdown_sections.py` (heading boundaries), `default.py` (unstructured partition + `chunk_elements`).
- `storage/` — `SqlGateway` seam (`gateway.py`: `PostgresGateway` thin wrapper, `DuckDbGateway` single-writer file driver); graph stores `PostgresGraphStore` (asyncpg + RLS), `AgeGraphStore`, `DuckDbGraphStore`, `LlamaIndexPostgresBackend`, `RagBackend` Protocol. Engine = `database.backend` (`postgres` default, `duckdb` local single-user); guide `docs/STORAGE-BACKENDS.md`, DuckDB mirrors in `migrations_duckdb/`.
- `rag/` — `OllamaEmbedder` (SHA256 cache, zero-vector fallback), `PgmlEmbedder`, `Reranker`. Hybrid lexical+vector search is SQL-side (`corpus.rrf_fusion`, migration 006) via `handlers/query_handler.py` (`QueryHandler`) + `research/retrieval.py`; no `HybridSearcher` class. Late chunking: `embedders/late_chunk.py` + `late_chunk_core.py` (full-doc forward pass, chunk-span pooling), gated by `embedder_selection.py` (`embedding.late_chunking` + `latechunk` extra + `qwen3-embedding*` model); pooled fallback recorded as `embedding_mode`/`embedding_note` in the ingest result.
- `ingestion/` — pluggable strategy registry, the ONE pipeline seam: `base.py` (`ChunkPlan`, `validate_plan`), `registry.py` (explicit > config map > detection > default), `strategies/` (`default`, `transcript`, `pptx_structure` slide-level, `web_structure` heading-section).
- `profiles/` — one authored YAML per corpus; blocks support `validate:` and `calibrate:` (guide `docs/HARNESS-PLAYBOOKS.md`). The calibrate sweep (#211) scores a family of up to eight query-time recipe candidates, Holm-corrected. Outcomes are `success`, `no_significant_winner`, and `insufficient_data`; only `success` writes the lock. Issue #187's single-candidate gap is closed. The author → measure → apply walk-through is `docs/VALIDATION.md`.
- `web/` — website ingestion: `fetch.py` (httpx + robots.txt + size/content-type caps + same-domain BFS crawl), `metadata.py` (OG/Twitter/JSON-LD/canonical), `sections.py` (element→section chunking), `settings.py` (`web:` config → `WebFetchConfig`). Entry: `tools/ingest_url_tools.py` (`ingest_url`; MCP/HTTP/socket/CLI `ingest-url`).
- `tools/` — MCP tools; `ingest_common.py` = pipeline orchestrator (partition → chunk → embed → extract → store); `ingest_tools.py` = thin MCP wrapper.
- `extraction/` — pluggable ontology extractor: `protocol.py`, `regex_backend.py`, `langextract_backend.py`, `pgml_backend.py`, `bert_backend.py`.
- `_setup/` — `install.py` (full-stack installer), `migrate.py`, `validate_configs.py`, `check_ci.py`.
- `migrations/` — `001_corpus_schema.sql` … `006_rrf_fusion.sql`.
- `config/ontology.yaml` — entity/relation type vocabulary.
- `mcp-configs/` — per-editor MCP configs (`claude-code.json`, `cursor.json`, `codex.json`, all legacy `"mcpServers"` key). Root `opencode.json` (OpenCode) uses the `"mcp"` key — **never `mcpServers` in OpenCode format**. Configs launch the MCP stdio server (`mcp_server.py`, `corpus-kb --transport stdio`); `validate_configs.py` checks their consistency.

## Import Convention (IMPORTANT)

Absolute package imports everywhere under `src/corpus_kb/` (e.g. `from corpus_kb.config import load_config`). Tests use absolute `from corpus_kb.xxx` (pytest top-level modules cannot use relative imports — intentional). No `from src.` imports — everything is `corpus_kb.*`.

## Data Model Conventions

- Internal models are `@dataclass`, not `pydantic.BaseModel` (`utils/models.py`); exception: `Ontology` (`ontology.py`, Pydantic validation).
- Every file: `from __future__ import annotations`. **No `type: ignore`. No `Any` where a real type works.** Python 3.11+ only.
- 250-line soft limit on source files — split past it.
- TypeScript (`ui_src/`) mirrors these rules: 250-line soft limit, no `any`, no `@ts-ignore`. Plain English strings live in `ui_src/src/strings/en.json`; `pytest -m ste` checks them.

## Test Conventions

- TDD for new features; `tests/` mirrors `src/corpus_kb/`.
- Integration tests mock Ollama (`OllamaEmbedder` catching `ConnectionError` → zero-vector fallback); suite degrades gracefully without Ollama.
- Needs `pip install -e .` (no `sys.path` hacks). `pytest-asyncio` dev dep; `asyncio_mode = "auto"` in `pyproject.toml`.
- Config-validation tests: `tests/test_validate_configs.py`, importing `src/corpus_kb/_setup.validate_configs` directly.
- LangExtract fixtures: SHA256-keyed JSONL in `tests/fixtures/langextract_recorded/` (deterministic CI, no live LLM calls).
- `conftest.py`: `pg_pool` (asyncpg pool) + `graph_store` fixtures; Postgres tests marked `@pytest.mark.asyncio`.

## Env Var Overrides

Runtime overrides of `config.yaml`, auto-merged by `corpus_kb.config.load_config()` (priority: env var > CLI flag > config.yaml):

| Variable | Config key |
|---|---|
| `CORPUS_KB_DATABASE_URL` | `database.connection_string` |
| `CORPUS_KB_EMBEDDING_MODEL` | `embedding.model` |
| `CORPUS_KB_EMBEDDING_DIMENSIONS` | `embedding.dimensions` |
| `CORPUS_KB_GRAPH_BACKEND` | `graph.backend` |
| `CORPUS_KB_TRANSPORT` | `server.transport` |
| `CORPUS_KB_PORT` | `server.port` |

Definitive 13-variable table: `docs/ADMIN.md`; code truth: `corpus_kb.config.KNOWN_CORPUS_KB_ENV_VARS`; `corpus-kb doctor` warns on set-but-unread `CORPUS_KB_*` vars. Every `CORPUS_KB_*` var must be consumed — no dead mappings. Ingestion/extraction config is `config.yaml` only: `ingestion.strategies`, `ingestion.default`, `extraction.backend`, `extraction.gliner.*`, `search.exclude_degraded`, `search.qa_context_in_fts` — no env override; guide `docs/INGESTION-STRATEGIES.md`.

## GraphStore Pattern (new backends)

Contract: abstract `GraphStore` in `storage/graph_store.py`; subclass it and implement all `@abstractmethod` methods (all async). Reference implementations: `PostgresGraphStore` (asyncpg + RLS, recursive CTE BFS), `AgeGraphStore` (Apache AGE openCypher `MATCH` when `graph.backend: age`). **Do not modify MCP graph tools** — they call the interface, not the implementation.

`PostgresGraphStore`: transactional writes via `@asynccontextmanager transaction()` (all graph writes atomic). Provenance tables (documents, chunks) with FK enforcement. Batch ops (`batch_add_entities`, `batch_add_relations`) with application-level validation. Relation cap `MAX_RELATIONS_PER_CHUNK = 10`.

## Branch & Commit Conventions

- Branches (gitflow, enforced on PRs by `.github/workflows/branch-validate.yml`): `(feature|bugfix|hotfix|docs|chore|release)/<slug>`. Slug = alphanumeric characters + hyphens (`[-a-zA-Z0-9]+`; no dots, no underscores). `renovate/*` (dependency bot, `.github/renovate.jsonc`) allowed with any slug. Protected: `main`, `master`, `develop`.
- Commits: conventional prefixes (`feat:`, `fix:`, `chore:`, `docs:`, …), capitalized, 10–500 chars. `commit-validate.yml` checks each line individually — use single-line messages, or prefix every body line.
- Safe prefixes: `feat|fix|docs|chore|test|refactor|perf`. Avoid `build:`, `ci:`, `style:`.

## Zero-Drift Program Standards (BINDING for all future work)

Defined in [docs/research/harness-zero-drift-program.md](docs/research/harness-zero-drift-program.md) (CI enforcement map there). Any plan, by any agent or human, MUST follow them; violating plans are amended before implementation. If a rule blocks legitimate work, change the rule in the program document first — never route around it silently.

1. **One operation, one handler, many surfaces.** Shared handler in `src/corpus_kb/tools/`; CLI/HTTP/socket/MCP are thin adapters. Never a second pipeline for the same operation.
2. **Registry-first updates.** Update the parity catalogue (`tests/test_surface_parity_deep.py`) and/or `surface_registry.py` FIRST, then the handler, then regenerate via `poe gen` (`gen_harness_pack.py`, method docs, UI manifest). CI regenerates and diffs; a stale artifact fails the PR.
3. **One envelope per operation family.** Surfaces may add only keys in the parity test's explicit `ALLOWED_EXTRA` map; undeclared keys on one surface fail CI.
4. **Errors go through the taxonomy.** Typed errors from `corpus_kb/errors.py`; one failure class = one HTTP status + socket code + CLI exit code. No catch-all `except Exception → 400` handlers.
5. **Layering is law.** Import direction `api → tools → handlers → storage`, enforced by import-linter. No pipeline logic in surface modules.
6. **Plain English (ASD-STE100) everywhere user-facing.** Short active sentences ≤ 20 words, simple words, no idioms — error messages, help text, doctor output, docstrings, docs. Enforced by Vale (docs) + `pytest -m ste` (Python strings, Flesch-Kincaid grade ≤ 9).
7. **Size and hygiene gates.** Source files ≤ 250-line soft limit; xenon complexity gates, vulture dead-code, deptry unused deps. Wave 4 (PR-F) adds the duplication ceiling `poe dup-check` (jscpd over src/, threshold 1.8 — ratchet down as clones die), the dependency CVE audit `poe audit-deps` (`uv audit --locked`; unfixable findings ignored in `uv.toml [audit]` with a written reason each), the report-only size ratchet `poe size-ratchet`, and the coverage floor `poe cover-floor` (baselines: `scripts/quality-baselines.json` — numbers only move the honest way: ceilings down when code shrinks, floor up when tests land).
8. **Run the gates through poe.** `poe lint`, `poe types`, `poe verify`, `poe gen`. CI runs the same targets — local bypasses get caught. Wave 4 adds `poe validation-gate` (ingest validators + mode semantics + determinism; ci.yml / validation-gate).
9. **Deterministic output.** Query/search results stably ordered with tiebreakers; harnesses must be able to snapshot them.
10. **Every new gate needs three things:** a `poe` target, a CI job calling it, and a map row (+ a line here). A local-only gate is not a gate.
11. **UI SPA gates (D17).** `ui-lint`, `ui-test`, `ui-build` (includes the bundle budget), `ui-licenses`, `ui-manifest` (wired into `poe gen`), `ui-e2e` (Playwright smoke + axe on a live throwaway server). Each needs a `poe` target, a CI job, and a map row per rule 10.

## Known Issues (check open GitHub issues before fixing)

- #17 (HIGH FEATURE) — zero-data-loss shutdown/restart with transactional ingest. Not implemented.
- #31 (MEDIUM FEATURE) — BERT/transformer NER. Partial via `extraction/bert_backend.py`.
- #2 (MEDIUM TASK) — PyPI publish. Not implemented.

## Repository Map

No `codemap.md` — orient via the structure section above + `docs/DEVELOPMENT.md`.

Default embedding model `nomic-embed-text` (768d); upgradeable to `qwen3-embedding:8b-q8_0` (4096d); PostgresML (`pgml`) for in-database embeddings.
