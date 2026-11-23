# Repo Hygiene + Branch Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the three-branch GitFlow+mirror model, path-gated Ollama CI, and all audit remediations, using many small cheap subagents.

**Architecture:** Four waves. W0 bootstraps branches/protection locally + via `gh api`. W1–W3 are small PRs into `develop`, each driven by one ≤60-line brief file under `.zcode/agent-tasks/` that points at the audit reports instead of pasting them. Merge order is sequential across waves; within W2 two file-disjoint PRs run in parallel.

**Tech Stack:** git + `gh api` (settings), GitHub Actions YAML, pytest markers, Markdown/Vale docs, poe gates.

**Spec:** `docs/superpowers/specs/2026-11-22-repo-hygiene-branch-model-design.md` (read it first; this plan argues from it).

**Evidence reports (never paste them into briefs — point at them):**
- `.zcode/agent-tasks/audit-devops-report.md` (workflows, protection §2, Ollama gate §5)
- `.zcode/agent-tasks/audit-stubs-docs-report.md` (stubs §1, doc falsities §2, org §3)

## Global Constraints

- Branch names `(feature|bugfix|chore|docs)/<kebab-slug>` (validator law); conventional single-line commits, prefixes `feat|fix|docs|chore|test|refactor|perf`.
- All PRs target `develop` once it exists (W0). Nothing pushes to `master`/`production` directly, ever.
- Plain English ASD-STE100 in all user-facing strings/docs; one canonical term per concept; docs top level = user-facing only (exception: `docs/superpowers/`).
- Python: `from __future__ import annotations`, no `type: ignore`, no new `Any`, 250-line soft limit. TS mirrors.
- Gates through poe (`poe lint`, `poe types`, targeted `pytest`). Zero new warnings/errors vs pre-change baseline.
- Brief files ≤ 60 lines; dispatch prompt = one line: `Read <abs path> and execute as written.`; `run_in_background: true` always; every brief carries the grounding boilerplate ("Use web search to ground your work and remove even 1% of doubt. After two failed attempts to solve any problem, search the web before trying again.") + checkpoint duty ("Re-read this file at every milestone and before the final report — appended sections are new work for you.").
- Build-agent concurrency ≤ 2. Never `git stash`. Never edit lockfiles.
- mimosa quirk: shell commands that name `src/` paths can be blocked — agents use Read/Edit tools on `src/`, not shell.
- Local pytest runs degraded (no Ollama) by default; the gate-open proof comes from remote CI (touching `ci.yml` arms the path gate).
- After every push: poll `gh run list --branch <branch>` every 20 min until green; failures fixed before wave close.

## Review Focus

1. **Marker scope drift** — the `ollama` marker must cover exactly the gate's 39 files; a rag-touching PR must still run all 464 tests. Pinned: T1.2 collect-only counts in both modes.
2. **INVALID required context** — a skipped required context blocks merges. The ratchet job must stay green (short-circuit) on gate-closed PRs. Pinned: T2.2's run-log check.
3. **Owner lockout** — wrong required-context names on `develop`/`production` make them unmergeable. Mitigated by order: bootstrap PR merges before strict protection applies (T0.3 < T0.4).
4. **production divergence** — if `production` ever diverges from `master`, an ff-only automation dead-ends. Pinned: T3.3 falls back to a merge-commit PR with a warning body.
5. **Link breakage** — renames/folds must not break the currently-zero broken links. Pinned: T2.2 link-check step + docs-quality workflow.

---

### Wave 0 — Bootstrap (main thread + 1 junior agent)

### Task 0.1: Local residue cleanup

**Files:** Modify `config.yaml:618-619`, `.gitignore`; Delete `docs/research/.mimosa/`, `src/corpus_kb/.mimosa/`, worktree `F:\Documents\ZCode\.zcode\.zcode\worktrees\50ca\Corpus-kb`.

- [ ] Revert `config.yaml` database block: `backend: postgres`, `connection_string: postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb`
- [ ] `git worktree remove` the stale 50ca worktree (detached b08884e; nothing unique — verify with `git -C <path> status --porcelain` first)
- [ ] Delete both `.mimosa/` dirs; append `.mimosa/` to `.gitignore`
- [ ] Verify: `git worktree list` shows one entry; `git status` shows only intended changes; `python src/corpus_kb/_setup/validate_configs.py` passes

### Task 0.2: Create branches, switch default (main thread — repo settings)

- [ ] `git branch develop master && git branch production master && git push origin develop production`
- [ ] `gh api -X PATCH repos/moliver28/corpus-kb -f default_branch=develop`
- [ ] Verify: `gh repo view --json defaultBranchRef` → `develop`

### Task 0.3: Bootstrap PR → develop

**Files:** Commit the 3 untracked `docs/superpowers/{plans,specs}/2026-11-17-*.md` + this plan + the spec; Modify `.github/renovate.jsonc` (`baseBranches` gains `develop`).

- [ ] Branch `chore/repo-hygiene-bootstrap` (from develop), commit, PR → develop, merge on green
- [ ] This PR touches no embedding path → proves gate-closed CI path end-to-end (Ollama steps absent in run log)

### Task 0.4: Protection on all three (main thread — owner-approved)

- [ ] `gh api -X PUT repos/moliver28/corpus-kb/branches/{master,develop,production}/protection`: required PR, required contexts = master's current 9 minus `agent-governance` (exact names from devops report §2), `dismiss_stale_reviews: true`, `enforce_admins: true`, `required_linear_history: true`, `allow_force_pushes: false`, `allow_deletions: false`, required approvals 0
- [ ] Verify: `gh api repos/moliver28/corpus-kb/branches/develop/protection` shows the ruleset; open+merge one trivial PR to develop as smoke

---

### Wave 1 — CI Ollama gating (1 hephaestus agent)

### Task 1.1: Program-doc amendment (FIRST, zero-drift law)

**Files:** Modify `docs/research/harness-zero-drift-program.md` (ratchet rule + enforcement-map row), repo `AGENTS.md` (CI paragraph).

- [ ] Rewrite the "ratchet keeps pulls unconditional" rule → "ratchet job always runs; gate-closed runs short-circuit green before any install; full models-up measurement on rag-touching PRs". Map row + AGENTS.md line updated in the same change.

### Task 1.2: `ollama` marker

**Files:** Modify `pyproject.toml` (markers list), all test files matching the `ollama_code` globs (`ci.yml:98-113`; devops report §5 names them: embedder, reranker, late-chunk, retrieval, MRL sets).

- [ ] Register `markers = ["ollama: needs live Ollama models; deselected when the CI path gate is closed"]`
- [ ] Module-level `pytestmark = [pytest.mark.ollama]` in each of the 39 gated files; agent reports back the exact file list
- [ ] Verify: `pytest -m "not ollama" --collect-only -q | tail -1` ≈ 5,081−464; `pytest -m ollama --collect-only -q | tail -1` ≈ 464

### Task 1.3: ci.yml leg + ratchet changes

**Files:** Modify `.github/workflows/ci.yml` (3 OS test legs; warning-ratchet job).

- [ ] Gate-closed legs: pytest command gains `-m "not ollama"` (parameterize on `needs.changes.outputs.ollama_live`)
- [ ] Ratchet job: every Ollama-install/pull/pytest step gains `if: needs.changes.outputs.ollama_live == 'true'`; add an always-on first step that echoes the skip notice when closed (context stays green, job never blocks)
- [ ] Local gates: `poe lint`, `poe types`, `pytest tests/ -m "not ollama" --durations=5` degraded-green
- [ ] Branch `chore/ci-ollama-skip`, PR → develop. Touching `ci.yml` arms the gate → remote run proves the gate-OPEN path (all 464 run with models). Merge on green.

---

### Wave 2 — Stubs + docs (2 parallel agents, file-disjoint; merge after W1)

### Task 2.1: Stub cleanup (sisyphus)

**Files:** Modify `src/corpus_kb/coding/coder_window.py:17-39` (+ its test file), `src/corpus_kb/extraction/__init__.py:96-97`, `src/corpus_kb/research/release/evidence_gates.py:6`, `ui_src/src/App.tsx:70-72`, `src/corpus_kb/_setup/install.py:475`.

- [ ] TDD: failing test for coder-window truncation/windowing contract items 3–5 (evidence: stubs report §1 row 1) → implement minimal truncation honoring the file's stated contract → pass
- [ ] Delete the `llamaindex` arm + its docstring mention (registry never exposes it)
- [ ] Fix 3 stale comments/docstrings; `install.py` TODO → implement the precedence test if ≤ ~30 lines, else file an issue and delete the marker comment
- [ ] Verify: `poe lint`, `poe types`, targeted pytest, `ui-test` if App.tsx snapshotted; PR `feature/stub-cleanup` → develop, merge on green

### Task 2.2: Docs truth + organization (sisyphus-junior)

**Files:** `docs/OPERATIONS.md`, `docs/INGESTION.md`, `docs/FEATURES.md`, `docs/FAQ.md`, `docs/ANALYSIS.md`, `docs/API.md`, `docs/DEVELOPMENT.md`; Create `docs/README.md`, `docs/research/README.md`; Rename `deployment.md`→`DEPLOYMENT.md`, `getting-started.md`→`GETTING-STARTED.md`, `understanding-your-report.md`→`UNDERSTANDING-YOUR-REPORT.md`; Delete `docs/research.md` (fold into `docs/research/README.md`); generator prose fix + `poe gen`.

- [ ] Fix every §2 falsity (10 items, anchors in stubs report §2 — doctor exit-3 + `--json`, dead `chunking.*` keys, 7 grammars, `ml` backend, 28 WATCH_SUFFIXES, 33 methods, P3 batch)
- [ ] Voice pass on touched pages ONLY: owner voice (direct, imperative, no marketing — INSTALL.md is the reference), one-line scope statement at page top, tables/diagrams over paragraphs, ≤20-word sentences
- [ ] `docs/README.md` = single entry map with "start here" paths; note the `docs/superpowers/` exception; TOCs on ANALYSIS-METHODS.md + PLUGIN.md; UPPER renames + every inbound link updated
- [ ] Verify: programmatic relative-link check (0 broken), `poe gen` drift-clean, Vale/docs-quality green; PR `docs/audit-fixes` → develop; run log must show ratchet green via short-circuit + `-m "not ollama"` deselect (pins Review Focus 2). Merge on green.

---

### Wave 3 — DevOps (1 hephaestus agent + owner-gated drill)

### Task 3.1: Workflow consolidation (one PR, three commits)

**Files:** Delete `.github/workflows/agent-governance.yml`; Modify `.github/workflows/docker-image.yml` (master push → `edge` tag only; `v*` tags → immutable version tags); Create `.github/workflows/production-sync.yml`.

- [ ] Grep for `agent-governance` references (badges, docs) and clean them
- [ ] production-sync: `on: release: [published]` → script opens PR master→production via `gh api` (ff-able), falls back to merge-commit PR with warning body on divergence (Review Focus 4); needs `contents: write`, `pull-requests: write`
- [ ] Verify: YAML lint, `gh workflow view` dry checks; PR `chore/devops-consolidation` → develop, merge on green

### Task 3.2: Release drill (OWNER-GATED — ask before tagging)

- [ ] Owner confirms → release PR develop→master, tag `v0.1.1rc0`, GitHub prerelease → exercises release.yml (TestPyPI) + changelog.yml + production-sync (PR appears)
- [ ] Merge the production PR (the deploy act); document 0.1.0 provenance (uploaded 2026-11-18, no matching run) in drill notes appended to the devops report

---

### Wave 4 — Close-out (main thread + momus)

- [ ] T4.1 **No-new-stubs sweep** (owner directive 2026-11-22): fresh read-only agent re-runs `.zcode/agent-tasks/audit-stubs-docs.md` as written against post-program `develop`. Diff findings vs the two baseline reports: every prior stub/false-doc finding must be CLOSED and nothing NEW may appear. Any new finding → fix or file an issue before program close.
- [ ] One whole-branch review: momus on `develop` vs program-start master (user review-cadence law), fix loop on findings
- [ ] `gh run list` all-green on every merged PR branch; protection triple-check via API
- [ ] Memory write-back (program state entry + MEMORY.md trim under 24.4KB); archive agent briefs to `.zcode/agent-tasks-archive/`
- [ ] File leftover out-of-scope findings as issues (PR #435 decision, full-docs voice pass, ERROR_MAP remaining types if deferred)

## Self-Review

- Spec §1→T0.2/T0.4/T3.1; §2→T1.1–1.3; §3→T0.1/0.3; §4→T2.1/2.2; §5→T3.1/3.2; orchestration rules→Global Constraints. No gaps.
- Steps are single-decision; no TBD lines; no transcript bodies.
- Marker name `ollama` consistent across T1.1–1.3; branch names consistent.
- Review Focus 1–5 each pinned to an owning task (T1.2, T2.2, T0.3<T0.4, T3.1, T2.2).
- Plan length proportionate to spec (no code transcripts — infra plan, steps carry exact values/commands).

## Amendment 2026-11-22 (post-T0.4 discovery)

branch-validate.yml (branch-validate.yml:14-20) matches ONLY GitFlow-prefixed head refs. GitFlow integration PRs — develop→master (release) and master→production (sync) — have head refs `develop`/`master`, which FAIL. Required context "Validate Branch Naming" would go red and block every release and production sync. Amendment: T3.1 also amends branch-validate.yml to accept bare `develop`, `master`, `production` head refs (integration branches; protected-branch PRs still need green checks). This is a validator-rule change, not a poe-gate rule change, so no program-doc row; AGENTS.md Branch section gets one sentence.
