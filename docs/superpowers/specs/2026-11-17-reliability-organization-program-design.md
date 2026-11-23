# Corpus-KB Reliability & Organization Program — Design

Date: 2026-11-17. Owner approved full scope ("everything, full go").

## Super-goal (owner, 2026-11-17)

**Net new end user ingesting a doc → corpus reaching minimum spec → end user
asking a question → answer time.** Every wave, cut, and fix scores by how much
it shortens that first-answer journey:

1. Install-to-ingest time (pip/docker/model setup)
2. Ingest-to-ready time (what "minimum corpus spec" means + surfacing it)
3. Ask-to-answer latency (retrieval width, judge entail shape, generation)

Existing decisions 1–7 remain; where one conflicts with journey time (e.g.
model pull excluded from install work), the item becomes an owner question in
the W4 decision package instead of a silent reversal.

## Owner-locked decisions (2026-11-17)

1. **Drivers:** runtime bug sweep + gate hardening + structure debt — all three.
2. **Auto-updates:** both halves — a `corpus-kb update` command (wheel and
   docker paths, apply migrations, verify schema, clean restart) AND repo-side
   bot automation. Renovate automerge for non-major updates is already live
   (`.github/renovate.jsonc`); the remaining repo-side work is a recurring
   security-triage bot for CodeQL/mimosa findings and optional Dependabot for
   GitHub Actions.
3. **Backward compatibility:** data + config aliases. Old Postgres and DuckDB
   stores from any past release upgrade cleanly through the migration runner;
   config keys keep legacy aliases (like `search.*`). API and plugin surfaces
   may still break with a version bump. No downgrade-support promise.
4. **Cuts:** audit first, owner approves each cut before removal. Cuts drive
   the dependency diet — dependencies drop with the features that need them.
5. **Install efficiency:** everything except model/weights pull time. The user
   knows how to pull models (or is willing to learn). Docker image size/build
   and wheel/pip weight are in scope.
6. **Execution:** risk-first spine (below), subagent-driven, off-peak for heavy
   waves, zero-drift standards binding throughout.
7. **LLM inference tokens (added 2026-11-17, owner directive):** cut LLM
   token spend across the product. Token-efficiency is a scoring axis in W4:
   prefer local/in-DB paths (gliner, pgml, regex, Ollama embeddings) over
   hosted LLM calls, cascade cheap backends before LLM, trim prompts, batch
   calls, reuse caches. The token audit (`llm-token-audit.md`) ranks the
   actions; owner approves the risky ones.

## Waves

### W1 — Bugs + gate blind spots (reliability, bug-resistance)

Stop the bleeding, then close the blind spots that let each bug class land.

PR0 (blocker, first): **#434 mimosa triage** — the L3 gate force-blocks all
commits from this machine until findings are triaged. False positives get
ledger entries (vendor bundles, localhost test servers); real findings get
fixed (`scripts/bench_vector_types.py` SQL parameterization;
`textbook_worker.py` SSRF caps documented or tightened).

Parallel batch (after PR0, disjoint file ownership, one worktree each):
- **#468** `bugfix/embed-read-timeout`: live `/api/embed` reads block far past
  `EMBED_TIMEOUT_SECONDS`. Bound the request batch size and enforce a hard
  per-request deadline so the zero-vector degrade path fires. Test with a mock
  slow server (bounded batch + stalled read), no live Ollama needed.
- **#455** `bugfix/duckdb-graph-id-space`: one entity-id space. The read-model
  projection must materialize entities under the aggregate's id (the id the
  CLI returns), so relations created from CLI-returned ids satisfy the
  documents projection FK instead of landing in the DLQ. Hops and search read
  the same model. DLQ stays manual; document the retry rule.
- **#436 + #430** `bugfix/umap-tiny-corpus`: guard `_reduce` below the
  spectral minimum (PCA fallback or `init="random"`, deterministic seed) with
  a 4-chunk regression fixture; version-gate `compatibility_layout` so both
  umap-learn 0.5.x and 0.6 work. Do NOT edit `uv.lock` (lockfile edits need
  owner approval); the version gate must keep the locked 0.5.12 green.
- **#412** `bugfix/entity-list-input-guards`: copy the chunks-list pattern
  (`tools/_count_input.py` `limit_arg`/`offset_arg`) into `list_entities`;
  reject `limit <= 0`, `offset < 0`, and out-of-int8 counts with the typed
  `ValidationError` on all four surfaces; pin with bad-input parity rows in
  `tests/test_surface_parity_deep.py`.

Test-infra batch:
- **#429**: conftest Ollama probe checks the configured model in `/api/tags`,
  not just the port; bound the psycopg pool connect timeout so fixtures fail
  fast with a typed error instead of hanging.
- **#413**: pin the environment inside `test_doctor_gpu_detected`
  (monkeypatch `CORPUS_KB_DATABASE_URL` and any DSN the doctor path reads).
- **#425**: scope the ui-e2e graph-workbench locator to the Entities card so
  the settle-count assertion stops matching sibling cards.

Gates batch:
- **#416**: derive `VALID_TOOL_NAMES` from `surface_registry.SURFACES` MCP
  tool names (single source of truth, no second list) plus a lockstep test;
  per zero-drift rule 10 the new gate gets a `poe` target, a CI job, and a
  map row in `docs/research/harness-zero-drift-program.md`.
- **#433**: the five P3 hygiene fixes as listed in the issue.

Deferred out of W1: #476 optional ready-signal hardening (moves to W5).

### W2 — Compat spine (small, enables W4/W5 safely)

- Migration matrix test: for a set of tagged past releases, build a store at
  that release's schema and prove the current migration runner brings it to
  head (Postgres and DuckDB sets). Start with the tags that exist; the matrix
  grows by one row per release.
- Config-alias lockstep test: every accepted legacy key (like `search.*`) has
  a declared alias mapping and a test pinning it, so aliases cannot silently
  die.

### W3 — Structure (organization)

- **#472**: break the 10 CodeQL cyclic-import clusters in the `_setup` module
  family.
- Oversized files: continue the per-file size-ceiling ratchet; split the worst
  offenders where touch is justified (`surface_registry.py` 1799,
  `config.py` 980, `doctor_research.py` 784, `release_handlers.py` 754).
- **#403** elegance backlog items; **#320** one `useMountRead` hook for the
  10 hand-rolled UI mount reads.

### W4 — Cuts + dependency diet + install efficiency

- Cut audit (read-only survey, starts in parallel with W1): score every
  surface, backend, strategy, extra, and CLI command by maintenance cost, test
  weight, dependency weight, and real usage; produce a ranked cut list.
  Owner approves each cut; removals land as PRs with migration notes to the
  replacement ("migrate it to what is").
- Dependency diet follows the cuts: candidates already visible — four
  llama-index packages (required today), `unstructured[md]`,
  `eventsourcing[postgres]`; move what survives the cut into optional extras.
- Install efficiency: docker image multi-stage/slim + layer cache; wheel size
  ratchet gate (numbers only move down); extras split so `pip install
  corpus-kb` stays light. Model pull stays out of scope per decision 5.
- **#458** CodeQL triage lands here if not consumed by the W5 sec bot.

### W5 — Update machinery + security automation

- `corpus-kb update` command: detect install flavor (pipx/wheel vs docker
  compose), pull the new version, apply migrations via the existing runner,
  verify schema, restart cleanly; `--dry-run` mirrors `setup --dry-run`.
- Boot-migrate verify hardening: on startup, probe migration state and refuse
  to serve if the store is newer than the code (clear typed error, no
  partial serve).
- Recurring security triage bot: scheduled workflow that runs CodeQL +
  dependency audit + mimosa scan, files triage issues (or auto-fix PRs for
  mechanical findings) on a weekly cadence.
- **#476** optional `pg_isready` real-server hardening lands here.
- Renovate automerge stays as configured; majors stay human-reviewed.

## Non-goals

- Model/weights pull-time optimization (owner decision 5).
- Full API/plugin compat freeze (owner decision 3).
- Downgrade/rollback support beyond existing rollback mirrors.

## Execution rules

- Zero-drift program standards binding (see repo AGENTS.md): one handler many
  surfaces, registry-first, error taxonomy, layering law, STE100, gates via
  poe with CI jobs and map rows, deterministic output.
- Subagent-driven: worker briefs in `.zcode/agent-tasks/`, dispatch with
  `run_in_background: true`, disjoint file ownership per worker, worktrees for
  parallel code changes, waves of at most 4 code-touching agents.
- Heavy waves run off-peak (peak = weekdays 14:00–18:00 UTC+8); local gates
  before push; poll remote CI to green before declaring done; squash-merge;
  one branch per PR, gitflow prefixes.
- `uv.lock` and other lockfiles are never edited without owner approval.
- Every wave ends with: issues closed or moved with a written reason, branches
  and worktrees cleaned, program ledger updated.
