# Harness Zero-Drift Program (October 2026)

Status: ACTIVE. Waves execute in order. Each wave merges as one squash PR with CI green.

## Purpose

A harness — a human, or an AI agent driving the system — must get the same answer
for the same operation on every surface. Today it does not. This program closes
every gap, then makes future gaps impossible to miss.

"Zero drift" means, for every operation:

1. Same parameters, same defaults, on CLI, MCP, HTTP, and socket.
2. Same result envelope shape on every surface.
3. Same error meaning: one failure maps to one HTTP status, one socket code,
   one CLI exit code, everywhere.
4. Help text, docstrings, and docs say what the code does. No stale claims.
5. Output is plain English (ASD-STE100): short active sentences, simple words.

## Audit findings (2026-10-14, four parallel surveys)

Confirmed defects, worst first. Evidence is `file:line` on master `2632368`.

| # | Defect | Wave |
|---|--------|------|
| 1 | MCP stdio is declared everywhere but does not exist. `--transport stdio` exits 2 (`cli.py:44`). `mcp[cli]` is a required dependency that no code imports. Default config transport is `stdio`. 16 of 33 "valid tool names" have no function. | 4 |
| 2 | GET `/api/release/status` calls `release_status` without `pg_pool` → TypeError → 400 on every call (`research_http.py:273`, `release_tools.py:120`). No test calls routes. | 1 |
| 3 | `list_documents` tool returns a hardcoded empty success (`ingest_tools.py:130`). HTTP and socket return real data. | 1 |
| 4 | Ingest-file runs two pipelines: CLI/tool (binary-safe, strategy-aware) vs HTTP/socket `command_handler` (UTF-8 text only, no pptx, different `source_type` meaning, extra envelope keys). | 1 |
| 5 | Socket drops params HTTP accepts: `search.source_type`, `add_relation.weight/metadata`. Unknown method maps to -32603, docstring promises -32601. | 1 |
| 6 | Projection catch-up runs on CLI only. HTTP/MCP callers get late projections. | 1 |
| 7 | HTTP error envelope: one catch-all 400 for everything, copied 28 times. `verify` alone uses 500 + `message`. `ingest-reembed` unavailable → 200; `ingest-status` unavailable → 503. | 2 |
| 8 | No cross-surface envelope comparison anywhere. Parity tests check existence only, never params, defaults, or envelope keys. | 2 |
| 9 | Query ordering is unstable: score-only ORDER BY, no tiebreaker. Identical queries can permute tied rows. | 2 |
| 10 | Oversized files: `cli.py` 1250, `ingest_common.py` 1050, `install.py` 1011, `query_handler.py` 941 (one 435-line method), `http.py` 867 (28× duplicated error envelope). Wired-pool bootstrap duplicated 4×. Default-tenant literal defined 6×. | 3 |
| 11 | Config rot: 11 dead keys. `CORPUS_KB_STORAGE_PATH` crashes config load (KeyError). 13 env vars undocumented. `docs/ADMIN.md` states wrong defaults. | 4 |
| 12 | No `src/corpus_kb/__init__.py`; no `__version__`; packaging uses classic find which needs it. PyPI (issue #2) blocked on structure. | 4 |
| 13 | Dimension-switch hazard: write path never pads or slices; schema comment claims a "max-capacity slot" that pgvector typmod does not honor. Default 768-dim model against `vector(4096)` column is unverified. | 4 |
| 14 | Doctor has no `--json` and always exits 0. Research cycle cannot run headless (interactive-only approval). Exit codes documented nowhere in help. | 5 |
| 15 | Docs: `harness-distribution.md` claims MCP-stdio ships. README/INSTALL say port 5432; compose publishes 5433. Hidden knowledge (default tenant, RLS GUC, ports, source_type vocabularies) appears in no doc. | 5 |

## Program standards

- **ASD-STE100** for all user-facing strings: error messages, help, doctor
  output, docstrings, docs. Short active sentences (20 words or fewer).
  Simple everyday words. No idioms. One instruction per sentence.
- **Ninth-grade rule**: a 9th-grade student can read the README quickstart and
  the output of every command, and know what happened and what to do next.
- **Registry-first**: the surface registry stays the source of truth for
  research/coding/release surfaces. The deep-parity catalogue (Wave 2) becomes
  the source of truth for cross-surface envelopes. Generators, not copies:
  `scripts/gen_harness_pack.py --check` stays green in CI.
- **Behavior-preserving splits** in Wave 3: pure moves. SQL moves byte-identical.
  Tests may change only where they pinned a defect.

## The zero-drift gate

`tests/test_surface_parity_deep.py` (Wave 1 seed, Wave 2 full):

- A catalogue lists every cross-surface operation with its arguments.
- The test calls the tool function, the HTTP route, and the socket method with
  identical arguments (stubbed database; no live Postgres in CI).
- It asserts equal envelope key-sets, minus an explicit `ALLOWED_EXTRA` map.
  An undeclared extra key on any single surface fails the test, with a diff.
- Wave 2 adds CLI `--json` to the comparison and grows the catalogue to all
  registered operations.

## How to add an operation after this program

1. Add the row to the deep-parity catalogue (or surface registry, for
   research/coding/release surfaces).
2. Implement the shared handler in `tools/` (this is the MCP surface).
3. Wire the thin CLI, HTTP, and socket adapters to call the same handler.
4. Run the generators (`gen_harness_pack.py`) and the parity tests.
5. CI refuses the PR if any surface drifts.

## Waves

### Wave 1 — Stop the lies (correctness)

Packages run in parallel worktrees; integrated onto `feature/zero-drift-w1`.

- **A route-truth**: fix `release/status` + its whole defect class; add
  `tests/test_http_route_smoke.py` so every declared route is called in CI.
- **B honesty**: real `list_documents`; honest `delete_document` (tenant
  honored, true cascade docstring, actionable error); socket passes
  `source_type`/`weight`/`metadata`; unknown method → -32601; all 9 socket
  methods covered by awaits tests.
- **C one-ingest**: HTTP/socket ingest-file goes through
  `tools.ingest_tools.ingest_file`; `aggregate_id`/`aggregate_version` become
  declared additive keys; one `source_type` meaning; unified
  `ingest_text` whitelist; catch-up symmetry in shared tool functions;
  parity seed test.

Done-when: full suite green, ruff clean, route smoke + parity seed in CI.

### Wave 2 — Contract layer

- Error taxonomy (`corpus_kb/errors.py`): typed errors; one mapping to
  HTTP status, socket code, CLI exit. All HTTP routes through one helper;
  the 28 copies die. `verify` and `ingest-reembed` status bugs fixed.
- Envelope helpers for search/list/graph/tags/sql/stats; the two ingest
  envelope families get names (`ingest.envelope.v1`, `research.envelope.v1`)
  and documented fields.
- Deep parity harness at full catalogue; CLI `--json` joins the comparison.
- `corpus-kb operations`: machine-readable inventory of every operation —
  surfaces, params, defaults, envelope keys, exit codes. Generated from the
  catalogue.
- Query determinism: stable tiebreakers on all score ORDER BYs.

### Wave 3 — Splits and dedupe (behavior-preserving)

- `cli.py` → `cli_support` + `cli_research` + `cli_coding`; `wired_pool`
  context manager replaces 4 copies of the bootstrap.
- `query_handler.py` → vector arms + CRUD modules; `tenant_connection`
  adopted; SQL moves byte-identical.
- `http.py` → route modules + single error helper; default-tenant constant
  unified to one definition.
- `ingest_common.py` → embed/extract/reembed modules; `install.py` →
  state/profiles/steps; `release_handlers`, `doctor_research`, `retrieval`,
  `surface_registry` get their natural seams.
- Dead code removed: `handlers/error_handling.py` (adopted or deleted),
  `reset_query_handler`, `_get_tenant_id`, `tenant_id_of`.

### Wave 4 — Interop and the real MCP server

- FastMCP stdio server: `--transport stdio` serves the registered tools.
  The `mcp` dependency becomes real. Editor configs (`mcp-configs/*`) work
  as shipped. `harness-distribution.md` becomes true.
- PyPI (issue #2): `__init__.py` + `__version__`; wheel + sdist build;
  clean-venv install test; `corpus-kb --version`.
- Config truth: dead keys removed or aliased; `CORPUS_KB_STORAGE_PATH`
  fixed; one env-var table; `graph.extractor` gains an `extraction.backend`
  alias; graph fallback logged, never silent.
- Dimension safety: verify the 768-vs-`vector(4096)` insert behavior on a
  live database; add validation with an honest error naming the fix;
  document the model↔schema matrix.

### Wave 5 — Plain language and harness ergonomics

- ASD-STE100 pass on errors, help, doctor `fix:` lines, docstrings.
- README quickstart rewrite (ninth-grade level); port 5432/5433 fixed;
  new `docs/OPERATIONS.md`: exit codes, ports, vocabularies, tenant/RLS
  contract, the full hidden-knowledge list, documented.
- `research cycle --approve` for headless runs; exit codes in `--help`.
- Regenerate harness pack; `gen_harness_pack.py --check` green.

## OSS tooling adoption

Chosen to enforce the standards above without duplicating existing gates
(ruff, pyright, syrupy, commitizen, CodeQL, mimosa are already in place).

| Tool | Enforces | Wave |
|------|----------|------|
| FastMCP in-memory `Client` | MCP surface joins the parity harness, tested in-process (no stdio subprocess) | 2→4 |
| OpenAPI 3.1 export from the catalogue (`corpus-kb operations --openapi`) | machine-readable API truth; input for schemathesis/oasdiff | 2 |
| schemathesis | property-based conformance fuzzing of HTTP routes against the spec | 2 |
| oasdiff | PR-time breaking-change diff of the spec | 2 |
| import-linter | layering contracts (api→tools→handlers→storage); kills shadow pipelines | 2 |
| poethepoet | `poe gen` / `poe verify` — the update workflow as commands, in pyproject | 2 |
| vulture | dead-code detection with a whitelist file | 3 |
| deptry | unused/missing dependencies (would have flagged the unused `mcp` dep) | 3 |
| xenon (or ruff C901) | per-function complexity gate | 3 |
| diff-cover | coverage gate on changed lines only | 3 |
| pre-commit | run the CI gates locally before push | 3 |
| Vale (+ official GitHub Action) | prose linting; custom "Corpus STE" style: ≤20-word sentences, STE100 banned words | 5 |
| textstat | Flesch-Kincaid grade ≤ 9 gate on docs; repo-native pytest enforces the same on docstrings/error strings (Vale cannot reach Python strings) | 5 |
| asd-ste100-skill (MIT, agent-side) | optional rewriter aid during the Wave 5 STE pass; not a CI gate | 5 |

Note: no mature standalone OSS ASD-STE100 checker exists (BSEC, HyperSTE,
Congree are commercial). Issue 9 of the spec is free to download from
asd-ste100.org and is the rule source for our Vale style + native checker.

## Issue mapping

- #84–#86, #80, #76 stay open (feature work, credential-gated; not this program).
- #22/#23 (governance, firecrawl-mcp-server) predate this repo's scope —
  propose closing as out-of-scope in the Wave 5 PR.
- New issues opened per wave for any deferred defect found on the way.
