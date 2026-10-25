# Harness Zero-Drift Program (October 2026)

Status: COMPLETE. All five waves shipped to master with green CI:
W1 `af00703` (#93), W2 `1348c2c` (#95), W3 `3f34819` (#96),
W4 `4c91db7` (#98), W5 `c690121` (#99). The standards below stay binding;
the CI enforcement map is live. The sentence-length ratchet is closed
(163 violations rewritten to 0; the Vale rule is now level: error).
Known ratchets: xenon D → C,
`uv.lock` regeneration (standalone dependency PR).

## Purpose

A harness — human or AI agent — must get the same answer
for the same operation on every surface. Today it does not. This program closes
every gap. Then it makes future gaps impossible to miss.

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
- **Ninth-grade rule**: a 9th-grade student can read the README quickstart
  and the output of every command. They can know what happened and what to
  do next.
- **Registry-first**: the surface registry stays the source of truth for
  research/coding/release surfaces. The deep-parity catalogue (Wave 2) becomes
  the source of truth for cross-surface envelopes. Generators, not copies:
  `scripts/gen_harness_pack.py --check` stays green in CI.
- **Behavior-preserving splits** in Wave 3: pure moves. SQL moves byte-identical.
  Tests may change only where they pinned a defect.

## UI SPA program amendments (D17, 2026-10-25)

The UI SPA program amends four rules of this document. The program
plan lives at `docs/research/ui-spa-program-plan.md`. The amendments
land in this document first, before any implementation.

1. **Code budget rule, program-scoped.** The "1% net-new code" target
   is suspended for this program. A hard budget replaces it: 8,000
   net-new lines or fewer (Python + TypeScript + strings; tests
   excluded). The per-area table in spec §16 of
   `docs/research/ui-spa-desk-research.md` (minus all agent rows) is
   the working estimate. Each PR reports its cumulative burn against
   the §16 areas. The SPA must not contain business logic; that rule
   is added alongside the budget. Overrun rule: 50% consumed before
   Task 6 opens → stop and re-scope with the owner. The same applies
   at 70% before Task 7 opens. Cut screens, never tests or gates.
   Test and fixture LOC appear in each PR report. They are visible,
   but ungated. Honesty clause: net LOC grows ~7×. A 1,046-line
   NiceGUI surface is replaced by a budgeted SPA. The end-state
   scorecard in the Definition of done carries the maintainability
   claim, not the line count.
2. **Hygiene rules extend to TypeScript.** The 250-line file limit
   and the ASD-STE100 plain English rule now apply to both languages.
   The ban on `any` and `@ts-ignore` mirrors the Python
   `type: ignore` ban. A new `pytest -m ste` file checks
   `ui_src/src/strings/en.json`.
3. **Five new gate rows.** Each gate gets a `poe` target, a CI job, a
   map row, and an `AGENTS.md` line per rule 10. The rows below follow
   the CI enforcement map format. `ui-build` includes the bundle
   budget. `ui-manifest` is wired into `poe gen`.
4. **Renovate.** The npm manager is enabled for `ui_src/`. Updates
   are grouped as `ui-deps` and run monthly. The solo owner keeps
   the churn low. Security updates run immediately.

| Gate | CI enforcement | Workflow / job | Lands |
|------|----------------|----------------|-------|
| UI lint | `poe ui-lint` | ci.yml / ui-lint | UI SPA (D17) |
| UI test | `poe ui-test` | ci.yml / ui-test | UI SPA (D17) |
| UI build | `poe ui-build`; includes the bundle budget | ci.yml / ui-build | UI SPA (D17) |
| UI licenses | `poe ui-licenses` | ci.yml / ui-licenses | UI SPA (D17) |
| UI manifest | `poe ui-manifest`; also runs in `poe gen` | ci.yml / ui-manifest | UI SPA (D17) |

## The zero-drift gate

`tests/test_surface_parity_deep.py` (Wave 1 seed, Wave 2 full):

- A catalogue lists every cross-surface operation with its arguments.
- The test calls the tool function, the HTTP route, and the socket method.
  The arguments are identical, with a stubbed database (no live Postgres in
  CI).
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
  honored, true cascade docstring, actionable error). The socket passes
  `source_type`/`weight`/`metadata`; unknown method → -32601. All 9 socket
  methods are covered by awaits tests.
- **C one-ingest**: HTTP/socket ingest-file goes through
  `tools.ingest_tools.ingest_file`; `aggregate_id`/`aggregate_version` become
  declared additive keys. One `source_type` meaning; a unified
  `ingest_text` whitelist; catch-up symmetry in shared tool functions.
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
  live database. Add validation with an honest error naming the fix.
  Document the model↔schema matrix.

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
asd-ste100.org. It is the rule source for our Vale style and native checker.

## CI enforcement map

Each rule lands in CI together with the tooling that implements it. A wave's
PR is not done until its job is green on GitHub Actions. CI calls the same
`poe` targets developers run locally (one source of gate commands).

| Rule | CI enforcement | Workflow / job | Lands |
|------|----------------|----------------|-------|
| Route liveness | `tests/test_http_route_smoke.py` runs in the test job; a dead route fails the PR | ci.yml / test | 1 |
| Cross-surface envelope parity | `tests/test_surface_parity_deep.py` in the test job; undeclared key on one surface fails with a diff | ci.yml / test | 1 (seed) → 2 (full) |
| MCP surface parity | FastMCP in-memory `Client` calls in the same parity test | ci.yml / test | 4 |
| OpenAPI spec freshness | CI regenerates `corpus-kb operations --openapi` and runs `git diff --exit-code` | ci.yml / contract | 2 |
| Breaking API changes | `oasdiff breaking` of PR spec vs master spec; failure blocks merge | ci.yml / contract | 2 |
| HTTP conformance | `schemathesis run` against the app with a fixed hypothesis seed | ci.yml / contract | 2 |
| Layering | `poe lint-imports` (`lint-imports`, contracts in `pyproject.toml`). TRUTHFUL AS OF 2026-10-20 (sp-suite-hardening): before that date this row was aspirational — no poe target and no CI step ran the contracts. Wave F added both and fixed the two violations the first run exposed (lazy `tools → server_wiring → api` chains in `tools/graph_read_tools.py` and `tools/query_tools.py`, now importing `storage.graph_factory` directly) | ci.yml / lint | F (wired; first declared Wave 2) |
| Gate commands | all CI jobs call `poe lint`, `poe types`, `poe verify`, `poe gen` — not raw commands | ci.yml (all jobs) | 2 |
| Dead code | `vulture src/` with `tests/whitelist.py`; new hits fail | ci.yml / lint | 3 |
| Unused / missing deps | `deptry src/` | ci.yml / lint | 3 |
| Function complexity | `xenon --max-absolute B` | ci.yml / lint | 3 |
| Coverage on changes | `diff-cover --fail-under 90` on the PR diff | ci.yml / test | 3 |
| Generated artifacts | `gen_harness_pack.py --check`, `cog --check docs/*.md` | ci.yml / contract | 2 (pack) → 5 (cog) |
| ASD-STE100 prose | Vale GitHub Action with `.vale.ini` + `styles/CorpusSTE`; `SentenceLength` is `level: error` (ratchet closed 2026-10-13: 163 warnings rewritten to 0) and blocks the PR | docs-quality.yml | 5 (error: w6a ratchet) |
| Grade level + string rules | `pytest -m ste` walks docs, tool docstrings, and error-message tables (Flesch-Kincaid ≤ 9, sentence ≤ 20 words) | ci.yml / test | 5 |
| Config truth | `validate_configs.py` extended: dead-key and env-var-table checks fail the build | ci.yml / validate-configs | 4 |
| Schema parity (DuckDB port) | `poe schema-parity` (`scripts/schema_parity_gate.py`, sqlglot 30.x static-only) folds `storage/schema.sql` + forward `migrations/` and `migrations_duckdb/` into final CREATE TABLE/SEQUENCE/INDEX states and diffs columns, PKs, uniques, FKs, sequences, and plain indexes. Undeclared drift fails the PR, and a STALE allowlist row (declared but no longer real) fails too. It does NOT cover dynamic EXECUTE-format DDL (declared in the gate allowlist), RLS/policies (PG-only by design), or runtime index managers (FTS/vss) | ci.yml / lint | G (issue #127) |
| Profile truth | `poe validate-profiles` (`src/corpus_kb/_setup/validate_profiles.py`): every `profiles/*.yaml` must parse against the loader schema, name only live ingestion strategies, reference only catalogue claim-template ids, and point `seeds_file` at an existing file. A profile that references a missing seam fails the PR (Living Corpus W5) | ci.yml / type-check step | W5 |

Rule for future waves: every new gate is (a) a `poe` target and (b) called by
a CI job. It is also (c) documented in this table and in `AGENTS.md`. A gate
that exists only locally is not a gate.

## Issue mapping

- #84–#86, #80, #76 stay open (feature work, credential-gated; not this program).
- #22/#23 (governance, firecrawl-mcp-server) predate this repo's scope —
  propose closing as out-of-scope in the Wave 5 PR.
- New issues opened per wave for any deferred defect found on the way.
