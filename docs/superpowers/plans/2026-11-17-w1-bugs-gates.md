# W1 — Bugs + Gate Blind Spots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 12 tracked runtime bugs and gate blind spots of wave W1, unblocking the git gate first.

**Architecture:** One PR per task, gitflow branches, disjoint file ownership so tasks PR0→T1–T6 can run as parallel worktree workers after PR0 lands. Each issue body is the detailed spec for its task — read it with `gh issue view <n>` before starting.

**Tech Stack:** Python 3.11.5+, pytest, httpx, DuckDB, asyncpg, Playwright (e2e only), poe gates.

**Spec:** `docs/superpowers/specs/2026-11-17-reliability-organization-program-design.md` (W1 section)

## Global Constraints

- Zero-drift standards binding: typed errors from `corpus_kb/errors.py`, one handler many surfaces, STE100 strings (≤20-word active sentences), 250-line soft limit, no `type: ignore`, no `Any`.
- Branch names: `bugfix/<slug>` or `chore/<slug>` (gitflow, no dots/underscores). Single-line conventional commit messages.
- No `uv.lock` or lockfile edits (owner approval required — not granted in this wave).
- Local gates before push: `poe lint`, `poe types` (venv interpreter), targeted pytest, `poe gen` if registry artifacts touched.
- English-only replies to owner; ASD-STE100 in all user-facing strings.

## Review Focus

- Timeout fix must degrade to zero vectors, never hang or crash the server: test pins the degrade path firing under a stalled read.
- Entity-id unification must not orphan existing DuckDB stores: projection change proven against a store built at the old id scheme.
- umap version gate must stay green on BOTH 0.5.12 (locked) and 0.6 (venv reality): test pins the gate branch.
- Input guards must hold on all four surfaces: parity rows pin CLI, HTTP, socket, MCP arms.
- VALID_TOOL_NAMES derivation must not accept names of non-MCP surfaces: test pins exact set equality with `SURFACES` MCP names.

---

### Task PR0: mimosa triage — unblock the git gate (#434)

**Files:**
- Modify: `scripts/bench_vector_types.py:130,163` (parameterize SQL)
- Modify: `src/corpus_kb/ingestion/strategies/textbook_worker.py:197` (document/verify fetch caps)
- Ledger: mimosa ledger entries for confirmed false positives (vendor bundles, `tests/test_textbook_worker.py:501/505`, `ui_src/scripts/record-fixtures.ts:67`)

**Interfaces:**
- Consumes: issue #434 checklist; mimosa ledger mechanism (recorder only — no auto-dismiss; if a ledger entry needs an owner decision, file/keep issue note instead).
- Produces: a green native pre-commit/pre-push path on this machine for all later tasks.

- [ ] Step 1: Run the mimosa scan (`mcp__plugin_mimosa_mimosa__security_scan` or CLI at `node .../mimosa/1.0.4/payload/dist/cli.js`) to reproduce the 9 high / 11 medium list.
- [ ] Step 2: Fix the two real code findings: parameterize the string-built SQL in `bench_vector_types.py`; verify + document the textbook worker's URL caps (scheme/size/allowlist) at the fetch site, or tighten if a cap is missing.
- [ ] Step 3: Write ledger entries for the confirmed-FP classes with evidence (vendor-bundle precedent #204, localhost fake-server test class).
- [ ] Step 4: Re-run scan; verify the gate no longer blocks a no-op commit from the main checkout. Branch `chore/mimosa-triage-unblock`, PR, CI green, squash-merge, close #434.

### Task T1: embed read timeout + batch bound (#468)

**Files:**
- Modify: `src/corpus_kb/rag/embedder.py` (batcher + client timeout path)
- Test: `tests/test_embedder_timeout.py` (new; mock slow server via `httpx.MockTransport` or local aiohttp stub)

**Interfaces:**
- Produces: `OllamaEmbedder` never blocks past the configured deadline; oversized batches split; on deadline the zero-vector fallback fires (existing contract).

- [ ] Step 1: Failing test — mock server that accepts the connect then stalls the read past `EMBED_TIMEOUT_SECONDS`; assert embed returns (zero vectors + note) within a bounded wall-clock (e.g. deadline + 5 s).
- [ ] Step 2: Second failing test — batch larger than the new cap splits into bounded sub-batches.
- [ ] Step 3: Implement: per-request deadline the httpx client cannot outlive (e.g. `httpx.Timeout(connect=..., read=EMBED_TIMEOUT_SECONDS, write=..., pool=...)` explicit all-four + a hard cap on `input` batch size in the batcher). Keep the SHA256 cache and zero-vector fallback behavior.
- [ ] Step 4: Targeted tests green + `poe lint` + `poe types`; branch `bugfix/embed-read-timeout`, PR, CI green, close #468.

### Task T2: DuckDB graph single id space (#455)

**Files:**
- Modify: the DuckDB entity projection (eventsourcing projections — locate via `advance_all_projections` call site)
- Test: DuckDB graph store test file (extend existing `tests/test_duckdb_graph*.py`)

**Interfaces:**
- Produces: projected `entities` rows carry the aggregate uuid as their id; `add-relation` with a CLI-returned id materializes (no DLQ); hops and `search-graph` read the projected model.

- [ ] Step 1: Failing test — add-entity via CLI, add-relation with the returned id, assert the relation row materializes and `documents` projection FK holds.
- [ ] Step 2: Regression test — a store built under the old dual-id scheme still upgrades/reads cleanly after the projection change (no orphaned rows without a written note).
- [ ] Step 3: Implement the projection id change; keep the events sidebar untouched.
- [ ] Step 4: Targeted tests + `poe schema-parity` (DuckDB set) + lint/types; branch `bugfix/duckdb-graph-id-space`, PR, CI green, close #455 (document the DLQ retry rule in the issue close comment).

### Task T3: umap tiny-corpus guard + version gate (#436 + #430)

**Files:**
- Modify: `src/corpus_kb/cluster_tools.py` (`_reduce`)
- Modify: `src/corpus_kb/coding/inductive_cluster.py:114` (version gate around `compatibility_layout`)
- Test: tiny-corpus regression under the inductive extra (4-chunk fixture)

**Interfaces:**
- Produces: `_reduce` never calls spectral init below its minimum (PCA fallback or `init="random"` with fixed seed); `compatibility_layout` passed only when umap-learn ≥ 0.6.

- [ ] Step 1: Failing test — 4-chunk corpus through `clusters_inspect` with umap installed: no `TypeError` from `scipy.linalg.eigh`.
- [ ] Step 2: Failing test — `UMAP.__init__` kwargs asserted against the installed version (gate branch: 0.5.x receives no `compatibility_layout`).
- [ ] Step 3: Implement both guards; deterministic seed pinned.
- [ ] Step 4: Targeted tests + lint/types; branch `bugfix/umap-tiny-corpus`, PR, CI green, close #436 and #430.

### Task T4: list_entities input guards, four surfaces (#412)

**Files:**
- Modify: `src/corpus_kb/tools/entity_read_tools.py` (`list_entities`)
- Modify: HTTP route (`api/routes_meta.list_entities`) + socket spec row (raw passthrough — guard owns the rule)
- Test: bad-input parity rows in `tests/test_surface_parity_deep.py` (pattern: `test_chunks_*` rows)

**Interfaces:**
- Consumes: `tools/_count_input.py` `limit_arg`/`offset_arg` (existing chunks-list pattern).
- Produces: `limit <= 0`, `offset < 0`, counts outside int8 rejected with typed `ValidationError` on CLI/HTTP/socket/MCP.

- [ ] Step 1: Failing parity rows for all four surfaces (bad limit −5, 0; bad offset −1; oversized count).
- [ ] Step 2: Wire `limit_arg`/`offset_arg` into the shared tool function + HTTP route; socket arm keeps raw passthrough.
- [ ] Step 3: `poe gen` if the registry manifest drifts; targeted parity tests + lint/types.
- [ ] Step 4: Branch `bugfix/entity-list-input-guards`, PR, CI green, close #412.

### Task T5: test-infra hardening (#429 + #413 + #425)

**Files:**
- Modify: `tests/conftest.py` (Ollama `/api/tags` model probe; psycopg pool connect timeout bound)
- Modify: `tests/test_installer.py::test_doctor_gpu_detected` (monkeypatch env)
- Modify: `ui_src/e2e/smoke.spec.ts` (scope the Entities-card locator)

**Interfaces:**
- Produces: fixtures fail fast with typed errors when Ollama lacks the model or the DB never answers; doctor GPU test holds in any shell; e2e settle-count assertion scoped to the Entities card only.

- [ ] Step 1: Reproduce locally (skip if env lacks services): hang in `test_cycle_fixture.py` with port-up-model-missing Ollama; assert the new probe fires the skip/typed error instead.
- [ ] Step 2: Implement the `/api/tags` model check + pool timeout bound.
- [  ] Step 3: Monkeypatch `CORPUS_KB_DATABASE_URL` (and DSN envs the doctor path reads) inside `test_doctor_gpu_detected`; run it in a shell with the var exported and without — both pass.
- [ ] Step 4: Rescope the e2e locator (`.ant-card` filtered to the Entities heading card, not `hasText` substring); `poe ui-e2e` green twice on this host.
- [ ] Step 5: Branch `chore/test-infra-hardening`, PR, CI green, close #429 #413 #425.

### Task T6: VALID_TOOL_NAMES lockstep gate (#416) + P3 hygiene (#433)

**Files:**
- Modify: `src/corpus_kb/_setup/validate_configs_toolnames.py` (derive from `surface_registry.SURFACES`)
- Test: lockstep test `tests/test_toolname_lockstep.py` (set equality)
- Modify (from #433): `analysis/method_register_progression.py:78-79`, `_setup/doctor_journey_state.py:134-135` docstring, `scripts/check_agent_configs.py:62-64` isinstance guard, `plugins/corpus-rag/soft_checks.py:85-87` cross-check, `tools/ana*` `cli_run_hint` guard (re-anchor per issue note)
- Gates: new `poe` target + CI job + map row in `docs/research/harness-zero-drift-program.md` (rule 10)

**Interfaces:**
- Produces: `VALID_TOOL_NAMES == {surface registry MCP tool names}` enforced by gate; the five #433 P3s closed.

- [ ] Step 1: Failing lockstep test (set equality against `SURFACES`).
- [ ] Step 2: Derive the frozenset from `SURFACES` inside `validate_configs_toolnames.py`; keep the exported name.
- [ ] Step 3: Wire `poe toolname-lockstep` + CI job + map row; `poe validation-gate` still green.
- [ ] Step 4: Land the five #433 fixes (each ≤ a few lines; follow issue re-anchor notes).
- [ ] Step 5: Branch `chore/toolname-lockstep-gate`, PR, CI green, close #416 #433.

---

## Sequencing

PR0 first (gate unblock). Then T1–T4 in parallel (disjoint files, separate worktrees). T5, T6 after or alongside — T5 touches `tests/conftest.py` (shared; land before or after the parallel batch, not during). Every task: local gates, PR, poll CI to green, squash-merge, close issues, clean branch + worktree.

## Definition of done (W1)

All 12 issues closed or moved with a written reason (#476 moves to W5). Master green. Gate list grew by the toolname-lockstep row. Program ledger updated.
