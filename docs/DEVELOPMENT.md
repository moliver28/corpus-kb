# Development Guide

This page is for contributors and anyone who wants to understand how Corpus-KB is built.

## Contents

- [Contents](#contents)
- [Project structure](#project-structure)
- [Quality gates (poe)](#quality-gates-poe)
- [CI pipeline](#ci-pipeline)
- [MCP config validation](#mcp-config-validation)
- [Architecture deep dive](#architecture-deep-dive)
- [Set up a development environment](#set-up-a-development-environment)
- [Running tests](#running-tests)
- [Conventions](#conventions)
- [PR workflow](#pr-workflow)
- [Governance checks](#governance-checks)
- [Next steps](#next-steps)


## Project structure

Single source tree; all new code goes in `src/corpus_kb/`.

```
<repo-root>/
├── config.yaml                    # Root runtime config
├── config/ontology.yaml           # Entity/relation type vocabulary
├── compose.yaml                   # Bundled Postgres stack for `corpus-kb setup`
├── opencode.json                  # OpenCode MCP config (root; uses the "mcp" key)
├── mcp-configs/                   # Editor MCP configs: claude-code, codex, cursor
├── pyproject.toml                 # Package metadata, dependencies, poe tasks
├── pyrightconfig.json             # Pyright config (basic mode)
├── scripts/                       # Gate scripts + backward-compat shims
├── ui_src/                        # TypeScript SPA source (React 19 + antd 6, pnpm)
├── tests/                         # Pytest suite (mirrors src/corpus_kb/)
├── docs/                          # User-facing guides (docs/research/ = frozen archive)
└── src/corpus_kb/
    ├── cli/                       # Typer CLI package (cli/__init__.py holds the app;
    │                              #   cli_*.py command modules)
    ├── api/                       # Starlette HTTP routes (http.py, routes_*.py) and
    │                              #   the JSON-RPC socket server (socket.py)
    ├── server_wiring.py           # Async startup: HTTP, socket, MCP, projections
    ├── config.py + config.yaml    # Config loader, env overrides, packaged defaults
    ├── contracts/                 # Operation catalogue shared by every surface
    ├── surface_registry.py        # CLI↔MCP surface map (parity catalogue rows)
    ├── mcp_server.py + mcp_tools.py # stdio server and the MCP tool registry
    ├── tools/                     # MCP tools; ingest_common.py = pipeline orchestrator
    ├── handlers/                  # Command/query/graph/coding handlers
    ├── domain/                    # Eventsourcing aggregates and application
    ├── projections/               # Async projections, checkpoints, DLQ
    ├── ingestion/                 # Strategy registry + strategies/ (default,
    │                              #   transcript, pptx, web, survey, markdown, code, textbook)
    ├── chunking/                  # Shared chunk primitives
    ├── rag/                       # Embedders, reranker, hybrid retrieval
    ├── storage/                   # SqlGateway seam, graph stores, backend modules
    ├── extraction/                # Ontology extractor backends
    ├── web/                       # Website fetch, metadata, section chunking
    ├── queue/                     # Durable ingest queue
    ├── research/ coding/ labeling/ classification/ analysis/
    ├── topics/ beliefs/ conflicts/ statements/   # Analysis + research subsystems
    ├── profiles/                  # One authored YAML per corpus
    ├── migrations/                # Idempotent SQL migrations 001-038 + rollback scripts
    ├── migrations_duckdb/         # DuckDB mirror migration set
    ├── ui/ + ui_dist/             # UI wiring + built SPA assets served at /app/
    ├── eval/ notebooks/ ask/      # Evaluation, marimo console, cited answers
    ├── errors.py                  # Typed error taxonomy
    └── _setup/                    # Installer, migration runner, config validators
```

## Quality gates (poe)

Run gates through poe (`pip install poethepoet`, then `poe <target>`). CI runs the same targets, so a local bypass gets caught. `poe check` = `verify` + `gen`: everything a PR must pass locally.

| Target | What it runs |
|---|---|
| `poe lint` | `ruff check` + format check over `src/` and `tests/` |
| `poe lint-imports` | Import layering contracts (`api → tools → handlers → storage`) |
| `poe types` | Pyright over `src/` (basic mode) |
| `poe dead-code` | Vulture scan (allowlist: `tests/whitelist.py`) |
| `poe deps` | deptry unused/missing dependency scan |
| `poe complexity` | Xenon complexity gate |
| `poe size-ratchet` | Function-length and file-length counts vs `scripts/quality-baselines.json` |
| `poe dup-check` | jscpd duplication ceiling over `src/` |
| `poe audit-deps` | `uv audit` dependency CVEs (ignore list: `uv.toml`) |
| `poe test` | Full pytest suite (Ollama/Postgres degrade gracefully) |
| `poe test-cov` | Suite with `coverage.xml` for the coverage gates |
| `poe cover-floor` | Overall coverage floor (needs `coverage.xml`) |
| `poe cover-diff` | Diff coverage of the changed lines vs `origin/master` |
| `poe verify` | lint + lint-imports + format-check + types + dead-code + deps + complexity + size-ratchet + dup-check + audit-deps + test |
| `poe schema-parity` | sqlglot schema parity across both migration sets |
| `poe validate-configs` | MCP config + runtime config consistency |
| `poe validate-profiles` | Profile schema + strategy/template/seed cross-refs |
| `poe validation-gate` | Ingest validators, modes, determinism (runs without optional extras) |
| `poe ste` | ASD-STE100 plain-language gate over docs, error strings, tool docstrings |
| `poe gen` | Regenerate/diff-check harness pack, method docs, UI manifest |
| `poe ui-lint` / `ui-test` / `ui-build` / `ui-bundle` / `ui-e2e` / `ui-licenses` / `ui-manifest` | SPA gates; see [UI.md](UI.md) |
| `poe hooks` | pre-commit over the whole tree |

## CI pipeline

`.github/workflows/ci.yml` runs these jobs on every PR and push:

| Job | What it does |
|---|---|
| `lint` | Ruff check + format, vulture, lint-imports, deptry, xenon, schema parity, size ratchet |
| `type-check` | Pyright over `src/`, then the profile cross-reference gate |
| `validation-gate` | Ingest validators and determinism checks, with optional extras NOT installed |
| `validate-configs` | MCP config files + runtime config consistency (see below) |
| `audit-deps` | Dependency CVE audit over the lockfile |
| `dup-check` | jscpd duplication ceiling |
| `gen-drift` | Regenerates generated artifacts and diffs them; a stale artifact fails the PR |
| `verify-postgres-image` | Signature check on the bundled Postgres image |
| `ui` | SPA build gates: lint, types, unit tests, build, manifest, bundle budget, licenses |
| `ui-e2e` | Playwright smoke + axe scan on a live throwaway server (REQUIRED job) |
| `test-ubuntu` | Full pytest suite with a Postgres service |
| `test-cross` | Full pytest suite on Windows and macOS |

The static gates run first: lint, then type-check, then validate-configs. The heavy legs (UI, e2e, cross-OS tests) run after. Ollama setup on CI runners is best-effort per OS; the suite degrades to zero-vector mode when it fails.

Additional governance workflows check branch naming, commit messages, and agent contribution rules. See `.github/workflows/`.

## MCP config validation

The validator (`src/corpus_kb/_setup/validate_configs.py`, target `poe validate-configs`) checks three things:

1. **Schema per format** — each config file is validated against its expected structure. The root `opencode.json` uses the OpenCode native format. Files under `mcp-configs/` are validated against their respective editor formats.
2. **Cross-config consistency** — all configs must define the same set of tools. Descriptions must match, and auto-approve lists must match where the format supports them.
3. **Fail-fast placement** — the job runs before the test legs. A config error stops CI early. The script exits with code 1 and prints every error it found.

Supported formats:

| File | Key | `command` field | `autoApprove` |
|---|---|---|---|
| `opencode.json` (repo root) | `mcp` (OpenCode native) | array of strings | required |
| `mcp-configs/claude-code.json` | `mcpServers` | string + `args` array | required |
| `mcp-configs/codex.json` | `mcpServers` | string + `args` array | required (copy-template for Codex CLI users) |
| `mcp-configs/cursor.json` | `mcpServers` | string + `args` array | not supported |

Every `autoApprove` name must be a real tool under `src/corpus_kb/tools/`. Run the gate locally with `poe validate-configs` and `pytest tests/test_validate_configs.py`.

To add a new editor format:

1. Create `mcp-configs/<platform>.json` in that platform's MCP server shape.
2. Add a validator function in `src/corpus_kb/_setup/validate_configs.py` (top-level key, required fields, `command` type, platform constraints).
3. Register it in the main validation loop.
4. Add cross-config checks if the format supports auto-approve or descriptions.
5. Cover it in `tests/test_validate_configs.py`: valid config passes; missing fields fail; wrong types fail; cross-config mismatch fails.

Common errors:

| Error | Cause and fix |
|---|---|
| Expected `mcp` key, found `mcpServers` | OpenCode format uses `mcp`. Rename the key, make `command` an array, add `type: "local"` and an `environment` object |
| Missing `autoApprove` | OpenCode, Claude Code, and Codex configs require the list; Cursor does not |
| Invalid tool name in `autoApprove` | The name must match a tool under `src/corpus_kb/tools/` |
| Mismatched descriptions | Every config must carry the same description per tool |
| Mismatched tool sets | Every config must define the same tool set |
| Wrong `command` type | OpenCode uses an array; the `mcpServers` formats use a string plus `args` |

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

### Layering

Import direction runs `api → tools → handlers → storage`. The import-linter contract (`poe lint-imports`) enforces it. Surface modules (CLI, HTTP, socket, MCP) are thin adapters; the shared handler under `src/corpus_kb/tools/` owns each operation.

### RLS isolation

Every query runs inside a transaction that first calls
`SELECT set_config('app.current_tenant_id', '<uuid>', true)`. Postgres RLS
policies filter rows by that setting. This keeps tenant data isolated
without changing SQL queries. The helper is
`src/corpus_kb/storage/tenant_conn.py`; the operator view (default tenant,
direct-SQL recipe) is in [OPERATIONS.md](OPERATIONS.md).

## Set up a development environment

```bash
git clone https://github.com/moliver28/corpus-kb.git
cd corpus-kb
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Install Postgres 17 with pgvector and AGE, create the database, and run migrations (`python src/corpus_kb/_setup/migrate.py`). See [INSTALL.md](INSTALL.md) for platform details. Or use the installer: `corpus-kb setup`. The DuckDB engine skips Docker entirely; see [STORAGE-BACKENDS.md](STORAGE-BACKENDS.md).

## Running tests

```bash
# All tests
poe test

# Ingest tests only
pytest tests/test_ingest.py -v --tb=short

# With coverage
pytest --cov=corpus_kb --cov-report=term-missing
```

Tests that need Ollama degrade to the zero-vector fallback when it is down. The suite then runs without a model server. Tests that need Unstructured `hi_res` mode are skipped on Windows because detectron2 is unavailable.

## Conventions

- Python 3.11+ only.
- Use `from __future__ import annotations` in every file.
- Absolute imports (`from corpus_kb.xxx import ...`); never `from src.`.
- Internal models are dataclasses, not Pydantic (`Ontology` is the exception).
- Prefer type hints. Avoid `Any` where a real type works. No `type: ignore` comments.
- 250-line soft limit on source files; split modules that grow past it.
- TDD for new features; tests mirror the `src/corpus_kb/` structure under `tests/`.
- User-facing strings follow plain-language rules (ASD-STE100); `poe ste` gates them.

## PR workflow

1. Create a branch from `master`. Allowed prefixes: `feature/`, `bugfix/`, `hotfix/`, `docs/`, `chore/`, `release/`.
2. Make atomic commits with conventional commit messages. The first line must start with a type prefix such as `feat:`, `fix:`, `chore:`, `docs:`.
3. Push and open a pull request.
4. Wait for CI: every job in the table above must pass on all OSes.
5. Merge only after all checks pass.

## Governance checks

The repository runs additional workflows:

- **Branch validation** - enforces naming conventions.
- **Commit validation** - checks commit message format.
- **Agent governance** - enforces rules for automated contributors.

See `.github/workflows/` for details.

## Next steps

- [Install guide](INSTALL.md)
- [Features overview](FEATURES.md)
- [Ingestion pipeline and strategies](INGESTION.md)
- [Admin guide](ADMIN.md)
- [Operations reference](OPERATIONS.md)
- [Storage backends](STORAGE-BACKENDS.md)
- [Browser UI](UI.md)
- [API reference](API.md)
- [FAQ](FAQ.md)
