# Repo hygiene + branch model design

Date: 2026-11-22. Status: awaiting owner review.
Evidence base: `.zcode/agent-tasks/audit-devops-report.md`, `.zcode/agent-tasks/audit-stubs-docs-report.md` (read-only audits, same day).

## Goal

1. Three long-lived branches — `develop` (integration), `master` (release-ready), `production` (deployed mirror) — with strict GitHub protection.
2. CI pulls Ollama and runs Ollama-dependent tests only when embedding/rag code changes.
3. All audit findings fixed: no stubs, no false doc claims, tidy docs, sane DevOps.

## Locked owner decisions (2026-11-22)

| Decision | Choice |
|---|---|
| Branch model | GitFlow + production mirror (fast-forward only) |
| Protection | Strict on all three: PR required, no force push, no delete, admins included |
| CI skip semantics | Gate-closed legs run `-m "not ollama"`; ratchet job short-circuits green when gate closed |
| Docs voice | Owner voice, visuals + scannability first, findable with zero repo knowledge |
| Orchestration | Many small-context subagents; briefs are pointers, not pasted content |

## 1. Branch and release architecture

```
feature/*, bugfix/*, chore/*, docs/* ──PR──> develop        (integration, full CI)
develop ──release/* PR──> master                             (release-ready; tag vX.Y.Z → PyPI)
master ──auto-opened ff PR──> production                     (deployed mirror; merge = deploy)
hotfix/* from production ──PR──> production + backmerges to master and develop
```

- Repo default branch becomes `develop`.
- Protection on all three (applied with `gh api`): required PR, required CI checks, no force push, no delete, linear history, admins included, dismiss stale reviews. Required approvals count stays 0 — a sole human cannot self-approve; the PR + green-checks bar is the gate. Strictness (`require up-to-date branch`) stays off for velocity.
- Required contexts after the change: today's 9 live contexts minus `agent-governance` (deleted, see §5). All required jobs must always run (a skipped required context is INVALID, not pass).
- `production-sync.yml` (new): on release published → opens the master→production PR. Merging it is the deploy act. No direct pushes to production exist.
- Release flow: tag on master triggers `release.yml` (prerelease→TestPyPI, final→PyPI via OIDC). Unchanged contract.
- Renovate: `baseBranches` gains `develop`.

## 2. CI Ollama gating contract

- `pyproject.toml` registers the `ollama` marker.
- The covered modules (embedder, reranker, late-chunk, retrieval, MRL — the set the `changes` job's 26 patterns already define) get module-level `pytestmark = pytest.mark.ollama`. Gated scope today: 39 files / 464 tests (~9.1% of 5,081).
- OS test legs: gate open → full suite with models (unchanged). Gate closed → same pytest command plus `-m "not ollama"`; install/pull steps stay skipped.
- Warning-ratchet job: runs on every PR (its required context stays valid). Gate closed → prints a notice and exits 0 before installing anything. Gate open → unchanged (models-up measurement, ceiling 0).
- ui-e2e: unchanged (proven degraded-green).
- Zero-drift program document `docs/research/harness-zero-drift-program.md` is amended FIRST (the "ratchet keeps pulls unconditional" rule changes); the enforcement-map row updates in the same PR. AGENTS.md CI section updates to match.

## 3. Repo organization and residue (W0)

- Local: revert `config.yaml` to `backend: postgres` + port 5433 DSN; delete stale worktree `F:\Documents\ZCode\.zcode\.zcode\worktrees\50ca\Corpus-kb`; delete `docs/research/.mimosa/` and `src/corpus_kb/.mimosa/`; `.gitignore` gains `.mimosa/` (wheel-inclusion risk).
- The 3 untracked 2026-11-17 superpowers docs are committed. Ruling: `docs/superpowers/` is the sanctioned internal design-record home (written exception noted in the docs rule).
- Spec + plan from this program land via the bootstrap PR (below); they are uncommitted until then.
- Branches: create `develop` + `production` from master; switch default branch to `develop`; apply protection (§1).

## 4. Stub and docs remediation (W2)

Stubs (fix, not hide):
- `coder_window.py`: implement the missing window/truncation contract items (silent context-overflow risk, P2).
- Remove the dead `llamaindex` extractor arm (P3, YAGNI).
- Batch P3: stale wave-2 docstring, stale `App.tsx` comment, `install.py:475` TODO → fix or file issue.

Docs truth (all findings in the stubs-docs report §2): doctor exit-code + `--json` claims (P1 ×2), dead `chunking.*` keys → point to `ingest.chunking.*`, tree-sitter list → 7 real grammars, FEATURES.md extraction table + `ml`, WATCH_SUFFIXES → 28 real suffixes, "30 methods" → 33 (fix generator prose, then `poe gen`), P3 batch (search_context caveat, migration count 046, graph/bfs deprecation, ERROR_MAP all 15 types, AGENTS.md commit-length misattribution).

Docs organization and voice (owner directive 2026-11-22):
- Docs must sound like the owner: direct, imperative, no marketing. Approved pages (INSTALL.md runbook style) are the voice reference.
- Visuals and scannability over prose: tables, diagrams, short sections; every page opens with a one-line scope statement.
- Zero-knowledge findability: new `docs/README.md` is the single entry map ("new here? start here" paths); generous cross-links; one canonical term per concept (existing law).
- `docs/research.md` folds into `docs/research/README.md` (collision fix); 4 lowercase files renamed to UPPER; inbound links updated (audit: zero broken links today — keep it that way).

## 5. DevOps fixes (W3)

- Delete `agent-governance.yml` (duplicate validators, contradicting prefix rules, un-failable checks, discarded audit log) + drop its required context.
- `production-sync.yml` (§1).
- Docker: master pushes stop rewriting the mutable version-style tag (`edge`/branch tags only); `v*` release tags stay immutable version tags.
- Release drill: run the dispatch-only TestPyPI dry-run once; document 0.1.0 provenance (uploaded 2026-11-18, no matching workflow run) in the drill notes.
- PR #435 (ui-crud-workbench) is real and open: out of scope, flagged for the owner.

## Orchestration model (cost rules, binding on all waves)

- Many small agents, not few big ones. One brief per bounded task; briefs ≤ 60 lines; dispatch prompt is one line pointing at the brief file.
- Briefs point at evidence (`audit-*.md` section anchors, file:line) — never paste report content.
- Concurrency ≤ 2 build agents (lesson: machine agent concurrency ≈ 2); same-tree when file-disjoint, worktrees when not.
- Background dispatch always; brief files carry grounding boilerplate + checkpoint duty (re-read brief at milestones).
- Cheap verified check wins: local gates (`poe lint`, `poe types`, targeted pytest) before push; remote CI polled every 20 min.

## Acceptance criteria

1. `develop` default, `production` exists, protection strict on all three (API-verifiable).
2. A PR that touches no embedding path runs zero Ollama steps and skips the marked modules; a rag-touching PR runs the full gated suite (both provable from the run logs).
3. Ratchet context green on a non-rag PR (short-circuit path).
4. All P1/P2 audit findings closed with PRs; P3 batch closed or filed as issues.
5. `docs/README.md` live, no broken relative links, renames + index in place.
6. TestPyPI dry-run executed once; provenance note committed.
7. Remote CI green on every merged PR; zero-drift gates unchanged except the amended ratchet row.
