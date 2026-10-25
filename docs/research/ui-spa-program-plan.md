# Corpus-KB UI SPA Program — Implementation Plan (Program 1: lifecycle SPA, no assistant)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the NiceGUI browser UI with an 8-stage lifecycle SPA (Setup → Ingest → Review → Scale → Ask → Analyze → Codebook → Release) served same-origin by the existing HTTP server, so a user controls Corpus without the CLI.

**Architecture:** React 19 + Ant Design 6 + TanStack Query SPA in `ui_src/`, built by Vite 8 into `src/corpus_kb/ui_dist/`, mounted by Starlette at `/app/` BEFORE NiceGUI (hash router). The SPA holds no business logic: it calls only existing `/api/*` routes plus three small additive backend packages (B2 read-lists, B3 upload, B4 doctor) built registry-first. The agentic Assistant is Program 2, deferred by owner decision 2026-10-25.

**Tech Stack:** TypeScript strict, Vite 8.3.x, pnpm, Node 24 LTS (`.nvmrc`), React 19, antd 6, `@tanstack/react-query`, `react-router` (hash), Vitest + Testing Library, Playwright (Task 9 only). Runtime stays pure Python.

**Spec:** [docs/research/ui-spa-desk-research.md](ui-spa-desk-research.md) (v1.2). This plan argues from it; executors read both. References like "spec D.1", "spec F4", "spec B2" point into that document.

## Decision register deltas (owner modal + web grounding, 2026-10-25)

| ID | Decision | Basis |
|---|---|---|
| Assistant | D11–D15 moved to Program 2. Program 1 has no `agent/` package, no SSE, no chat UI. | Owner (modal): defer. Program-2 grounding recorded now: Pydantic AI v2 stable (v2.59.x, June 2026) works with Ollama tool calling; its AG-UI adapter is weak → use the spec D.9 own-SSE contract; assistant-ui is actively maintained with tool-UI + inline-approval primitives (still the chat-UI pick). Re-verify licenses at Program-2 start. |
| D2 | APPROVED: Node 24 LTS + pnpm + Vite 8, lockfile committed, build-time only. | Owner (modal). Vite 8 stable 8.3.x (needs Node ≥ 20.19); Node 24 = Active LTS. antd 6 supports React 19 natively (no v5-patch package). |
| D16 (new) | Staged overlap: SPA-only tasks start now in a fresh worktree off master. Task 5 (B2–B4 backend) starts only after the in-flight cli-reorg PR merges; rebase `feature/ui-spa` first. | Owner (modal). `AGENTS.md` + `pyproject.toml` are dirty in the main tree from that session; those are Task 2/3 touchpoints. |
| D17 (new) | Zero-drift amendments (below) land in PR-1, changing the program document first — never routed around. | Owner (modal): the pivot is the zero-drift rules. |
| D9 | CLOSED 2026-10-25 (owner, in chat): NiceGUI dies at program start. PR-1 mounts the SPA AND deletes NiceGUI in the same PR (never zero UI). No flip task, no soak, no freeze, no `ui.frontend` key. | Owner ruling supersedes spec §15/D9 gradual path. |

## Program goal update (owner, 2026-10-25): investor-ready first draft

1. The program's first deliverable is a **first-draft UI showable to investors**. Inside Task 6, the demo slice lands first, in this order: **Home** (corpus summary when documents exist; stepper otherwise) → Ingest "Load sample knowledge" → Ingest Files drag-drop upload (live transformation beat) → Ask cited answer with hover-citation highlight + verify badge → one honestly-badged "not verified" answer → topics bar-list + one analysis → method advisor. That state is the "investor draft" milestone; the remaining screens complete the task. If the demo date lands before Task 7, the advisor and the release-refusal trust beat (409 gate renders as a structured refusal) pull forward into the slice — both are self-contained.
2. A **demo script** (10 minutes, ≤ 8 steps, plain English, no jargon) is drafted in Task 6 and verified live in Task 8. It replaces the NiceGUI parity pass — spec Appendix F is void (NiceGUI is gone). The script pins exact question text, expected answers, per-step timings, and one fallback line per step.
3. Visual identity is a gate, not a nicety: the `frontend-design` skill loads in every screen-builder brief. Concrete moves for THIS product: two self-hosted OFL font families via `fontFamily` tokens (humanist sans for chrome; serif reserved for evidence text — cited sentences get a documentary voice); deep-ink palette with ONE highlighter accent used only on citations/evidence marks (color literally means "this is grounded"; antd default blue forbidden); signature interaction = hover a cited sentence → its source passage highlights and scrolls into view (verification-as-trace IS the product); topics/analysis render as ranked hairline bar-lists in plain markup (no chart lib); empty/loading states shaped like evidence cards, not generic bars.
4. Demo environment rules (Task 5a/8 enforce): qwen3 4096d embeddings (the dims<1024 warning must PASS on stage), `[topics]` extra installed, corpus seeded with 100–300 deterministic synthetic docs (5 files is not scale proof), embeddings AND one generation pre-warmed before the room fills, no live internet, degraded paths render honest badges, deterministic ordering on, a 1024-pixel-wide layout pass, and a screen-recorded backup of the full demo committed to the runbook.
5. Hash router stays for Program 1 (zero server change); history mode is post-demo polish via one fallback route.
6. Jargon defense on demoed screens: server `message` strings render only inside ReceiptCard notes, never as headlines; no config keys, exit codes, or status-enum words visible on demoed screens (demo-pass string audit in Task 8); Setup hides behind a collapsed "System ready" strip when doctor checks pass.
7. Ask uses `useLongOp` too: elapsed-time display and cancel during generation (a bare spinner for 10+ seconds reads as a hang to investors).

## Global Constraints

- Node is build-time only. Node 24 LTS pinned in `ui_src/.nvmrc` + `engines`. pnpm lockfile committed. Runtime Python never imports Node output except static files.
- SPA at `/app/`, hash router, same-origin only. CORS stays `["http://localhost:3000"]`, GET/POST (`api/http.py:264-265`). Never widen.
- SPA holds no business logic. Sole exception: `verifyBadge` (spec D.4 rows) — and its thresholds are NOT ported as literals: hoist `VERIFY_FULL_GROUNDEDNESS` (0.99) from `ui/view_models.py` into `config.py` defaults in PR-1, before that file is deleted; `gen_ui_manifest.py` emits `ui_src/src/api/thresholds.generated.ts` from those defaults (compile-time types, covered by the `poe gen` no-diff check). `/app/runtime-config.json` carries only runtime-tunable keys (`ui.spa.*`, `server_version`) — no policy constants over the wire. Degrade path, test-pinned: missing/invalid thresholds → badge renders `not verified` plus a plain-English note; never a crash, never a silent pass. No numeric policy literal anywhere in `ui_src/`.
- No existing route, envelope, CLI, MCP, or socket behavior changes. New routes are additive thin adapters; registry-first ordering (spec rule 2) on every new op.
- No new `CORPUS_KB_*` env vars. New config keys only: `ui.spa.{poll_ms,request_timeout_s,max_upload_mb}` (no `ui.frontend` — the SPA is the only UI); both `config.yaml` files kept identical; validation refuses bad values in plain English.
- TypeScript: strict, no `any`, no `@ts-ignore`; files ≤ 250 lines **soft** in TSX (imports plus JSX density; state the exception in the PR when a screen must exceed); all own user-facing strings in `ui_src/src/strings/en.json` (sentences ≤ 20 words, Flesch-Kincaid ≤ 9, `{placeholders}` stripped before scoring); antd's own component copy (set `ConfigProvider locale` once) is out of STE scope.
- Licenses: production deps MIT / Apache-2.0 / BSD / ISC / 0BSD only; devDependencies (test tooling, axe-core) exempt from the gate but recorded. No paid APIs, no telemetry.
- Branches `feature/<slug>` (no dots/underscores); single-line conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`), capitalized, 10–500 chars.
- antd usage policy (keeps the next antd major cheap): core stable components only (Layout, Menu, Table, Form, Input, Select, Button, Modal, Tabs, Tooltip, Tag, Progress, Upload, Switch, Checkbox, Card, Statistic-style big-number markup, message/notification) + `@ant-design/icons` (color is never the only status signal); theming via `ConfigProvider` design tokens only; no `@ant-design/pro-components`, no cssinjs runtime styling, no CSS-in-JS libraries — ONE static plain-CSS layer is allowed for the signature treatments (citation highlight, serif evidence text, hairline bar-lists); fonts are self-hosted OFL woff2 assets (offline demo; they join the license gate and the bundle budget). Any other antd API needs a plan amendment first. Recorded in `docs/UI-DEVELOPMENT.md` (Task 9).
- `ui_src/pnpm-lock.yaml` changes only as the output of `pnpm install/add/remove/update` or a Renovate PR — never hand-edited (repo lockfile rule). Manual bumps ride the PR that needs them, reason stated there.
- Install pnpm directly (CI action or standalone installer), never via corepack — corepack is leaving Node distributions and must not be a load-bearing step.
- Stale-SPA defense: `/app/` index.html served with `Cache-Control: no-cache`; `runtime-config.json` carries `server_version`; the SPA compares it to its build version at boot and shows a plain-English refresh banner on mismatch.
- NiceGUI is deleted in PR-1 together with the SPA mount (D9 closed). Its ~200 leaked-LOC concerns die with it or move server-side; nothing gets patched in a dead surface.
- Zero regression: every PR keeps `poe verify` + full pytest green with zero new warnings. The CLI/MCP/HTTP/socket surfaces are untouched by the NiceGUI deletion (its tests are deleted with it in PR-1; `test_ui_spa_mount.py` replaces the wiring coverage).
- Every subagent brief carries the web-search rule verbatim (see Brief template).
- Worktree: `F:\Documents\ZCode\Corpus-kb-ui-wt`, branch `feature/ui-spa`, cut from master. Never touch the two in-flight sessions' files (`src/corpus_kb/cli*`, `AGENTS.md` beyond Task 2's gate lines, `pyproject.toml` only in Task 3's additive blocks) before Task 5's rebase.

## Zero-drift amendments (D17 — edit `docs/research/harness-zero-drift-program.md` + `AGENTS.md` in PR-1)

1. **Code budget rule, program-scoped:** the "1% net-new code" target is suspended for this program and replaced by a hard budget: **≤ 8,000 net-new lines** (Python + TS + strings, tests excluded), with the per-area table in spec §16 (minus all agent rows) as the working estimate, reported per PR. The SPA-must-not-contain-business-logic rule is added alongside it. Overrun rule: cumulative burn is reported per PR against the §16 areas; 50% consumed before Task 6 opens, or 70% before Task 7 opens → stop and re-scope with the owner (cut screens, never tests or gates). Test and fixture LOC ride alongside in each PR report — visible, ungated. Honesty clause: net LOC grows ~7× (a 1,046-line NiceGUI surface is replaced by a budgeted SPA) — the maintainability claim is carried by the end-state scorecard in the Definition of done, not by line count.
2. **Hygiene rules extend to TypeScript:** 250-line file limit, no `any`/`@ts-ignore` (mirrors no-`type: ignore`), and the plain-English STE rule for `strings/en.json` (new `pytest -m ste` file) are now stated as applying to both languages.
3. **New gate rows** (each gets poe target + CI job + this map + an AGENTS.md line, per rule 10): `ui-lint`, `ui-test`, `ui-build` (includes bundle budget), `ui-licenses`, `ui-manifest` (wired into `poe gen`).
4. **Renovate:** npm manager enabled for `ui_src/`, grouped as `ui-deps`, monthly (solo owner — churn discipline), security updates immediate.

## Review Focus (uncovered failure modes; each pinned by a test in the named task)

1. HTTP 200 whose payload `status` is not success (`error`, `failed`, `empty`, `degraded`, `partial`, `abstained`) — must render a receipt or note, never a false success or a crash. → Task 4 normalizer table test.
2. Long synchronous POST (research cycle, directory ingest) with the tab closed mid-call — UI must show "unknown outcome", offer status polling, never auto-retry a POST. → Task 4 `useLongOp` tests; manual check in Task 9.
3. NiceGUI `ui.run_with(app)` mounts at `/` and shadows `/app` if mounted first — mount order must be SPA-then-NiceGUI in `both` mode. → Task 3 mount-order test.
4. Fresh `pip install` with no Node: missing `ui_dist` must log ONE line and keep the API fully working. → Task 3 degradation test.
5. Backend payload drift or unknown shapes — `status: "unknown"` fallback plus JsonViewer; recorded fixtures catch drift. → Tasks 4 and 6–8 fixture contract tests.

## Coordination model (D16)

- One program branch `feature/ui-spa` in worktree `F:\Documents\ZCode\Corpus-kb-ui-wt`. One PR per task (stacked, merged sequentially to master so each PR's CI is the full gate suite).
- Parallel builders within a task share the worktree with **disjoint file-ownership lists** (listed per task) and bounded-output briefs. The integrator (orchestrator) runs `poe verify` + `poe ui-test` before each PR.
- Task 5 is blocked until the cli-reorg PR merges. Tasks 6–8's screens that need B2/B4 routes (upload tab, review queue, doctor panel) are built in the same task as their route but merged after it (they are stacked behind Task 5 anyway). Screens that need nothing new (Ask, Scale ledger, Analyze via manifest, topics/documents lists) are not blocked.
- No fake backend, ever: screens develop against the real server; routes that have not landed render the designed `unavailable` OpRunner state (that state is product behavior, not scaffolding); the single fetch-level vitest double in `ui_src/src/test/` is the only fixture source. No standalone mock server, no second pipeline.
- One PR open at a time. This repo squash-merges, so after each merge the integrator rebases the remaining stacked branches onto master before opening the next PR — otherwise child PRs show parent commits and review noisy diffs.
- After any push: poll `gh run list --branch feature/ui-spa` every 20 min until green; failure → `gh run view <id> --log-failed` → fix → push again.

## Subagent brief template (prepend to every builder brief)

> After 2 failed fix attempts on the same problem, you MUST run a web search and ground the fix before attempt 3. Record the finding in the PR description.
> You own ONLY these files: `<list>`. Do not edit files outside the list; report needed changes instead.
> Every file ≤ 250 lines; TS strict, no `any`, no `@ts-ignore`; Python: absolute imports, `from __future__ import annotations`, no `type: ignore`, no `Any`.
> User-facing strings go in `ui_src/src/strings/en.json` only; sentences ≤ 20 words.
> Zero new warnings. Run your gates: `<per-task gates>`. Commit single-line conventional style on `feature/ui-spa`.

## UI engineering tooling (wire into execution; no UI LSP exists in this harness)

There is no TypeScript language-server or diagnostics tool in this session's tool registry — type safety is enforced the same way the repo enforces Python (pyright via `poe types`): `tsc --noEmit` + ESLint strict via `poe ui-lint` locally and in the CI `ui` job. Do not skip the gate because an LSP is absent.

| Tooling | Kind | Where it plugs in |
|---|---|---|
| `engineering-skills:senior-frontend` | skill | Load in the brief of every builder touching `ui_src/` (Tasks 3, 4, 6, 7, 9) — component structure, antd 6 idioms, React Query patterns. |
| `engineering-skills:code-reviewer` (or `momus`) | agent | Review gate on every PR per the standing zero-regression standard; whole-branch review before the final PR. |
| `browser-use:web-gui-tester` + `control-browser` | skills | Task 8 parity walk and post-PR-4/PR-5 live smoke against a running server (`corpus-kb start` + demo-kb). Backing browser MCP is not connected in this session — the skills drive Playwright through Bash; Task 9's committed Playwright suite is the deterministic fallback and source of truth. |
| `multimodal-looker` | agent | Builder self-verification: attach rendered-screen screenshots for layout/composite defects before marking a page task done. (`visual-judge` agents are NOT applicable — their charter excludes web UIs.) |
| `a11y-audit` | skill | Task 9 accessibility pass alongside the axe-core scan; findings become fix commits on PR-6. |
| `mcp__node_repl__js` | MCP tool | Ad-hoc node-side checks (inspect built bundles, verify manifest JSON shape) without leaving the session. |

Installed personal skills (2026-10-25, vetted, in `~/.zcode/skills/`, live from the next session — cite them by name in builder briefs alongside `senior-frontend`):

| Installed skill | Source | Where it plugs in |
|---|---|---|
| `react-best-practices` | vercel-labs/agent-skills (MIT, 70 rules + `rules/` files) | Every `ui_src/` builder brief (Tasks 3, 4, 6, 7, 9) — perf rules; ignore the Next.js/server-rule subset (we are a Vite SPA). |
| `composition-patterns` | vercel-labs/agent-skills (MIT, incl. React 19 no-forwardRef) | Tasks 6–7 builders — OpRunner/screen component APIs. |
| `web-design-guidelines` | vercel-labs/agent-skills | Task 8 parity walk + every review gate — UI review checklist. |
| `frontend-design` | anthropics/claude-code official plugin | Task 6 screen builders — distinct visual direction so the SPA does not read as stock antd templates. |
| `use-tanstack` | pleaseai/claude-code-plugins tanstack plugin | Task 4 after `pnpm install`: run `@tanstack/intent` to load the official TanStack skills matching the exact installed `@tanstack/react-query` version, per that skill's loader table. |

Rejected from the same list, with reasons (do not re-suggest): `callstackincubator/agent-skills` + its `add-skill` duplicate (100% React Native/TV), `affaan-m/ecc` (unvetted 100+ skill personal grab-bag), `antfu/skills` (Vue/Nuxt-centric; our Vite config is already fully spec'd), `@tanstack/ai-skills` npm package (a chat-middleware library for TanStack AI apps, not agent skills — wrong layer; revisit only if Program 2 ever switches from Pydantic AI).


---

### Task 1: Program docs + worktree (PR-1 part 1)

**Files:**
- Create: `docs/research/ui-spa-desk-research.md` (copy from `C:\Users\moliv\Downloads\Delete\corpus-kb-ui-implementation-plan(1).md`), `docs/research/ui-spa-program-plan.md` (this file)

**Steps:**
- [ ] Create worktree: `git worktree add F:\Documents\ZCode\Corpus-kb-ui-wt -b feature/ui-spa master`; copy both docs into `docs/research/`; commit `docs: add ui spa desk research and program plan`
- [ ] PR-1 opened from `feature/ui-spa`; CI green (docs-only PR still runs the full suite)

### Task 2: Zero-drift amendments (PR-1 part 2)

**Files:**
- Modify: `docs/research/harness-zero-drift-program.md` (amendments section above), `AGENTS.md` (add: `ui_src/` + `ui_dist/` layout lines, four new gate lines, TS hygiene line)

**Steps:**
- [ ] Apply the four amendments verbatim; AGENTS.md stays within its compressed size ethos (≤ 8 added lines)
- [ ] Vale green on both docs; commit `docs: amend zero-drift program for ui spa gates and budget`

### Task 3: Scaffold + mount + NiceGUI deletion (PR-1 part 3) — 1 builder

**Files:**
- Create: `src/corpus_kb/ui/spa.py` (body = spec D.1), `scripts/gen_ui_manifest.py` placeholder NOT here (Task 4), `ui_src/` scaffold (`package.json`, `pnpm-lock.yaml` committed, `tsconfig.json` strict, `vite.config.ts` = spec D.6, `.nvmrc` = `24`, `index.html`, `src/main.tsx`, `src/App.tsx` with the 8-stage `StageShell` stepper skeleton + `/app/#/setup` empty Setup page), `tests/test_ui_spa_mount.py`
- Modify: `src/corpus_kb/config.py` (hoist `VERIFY_FULL_GROUNDEDNESS` from `ui/view_models.py` to defaults FIRST; new `ui.spa.*` keys; refuse bad values), both `config.yaml` (drop the NiceGUI settings block, add `ui.spa.*`), `src/corpus_kb/server_wiring.py` (call `mount_spa`; remove the NiceGUI mount, `UiContext`, and the `load_ui_settings` gate), `pyproject.toml` (remove `nicegui` from the `[ui]` and `dev` extras — uvicorn stays; spec D.7 package-data `ui_dist/**/*`; poe targets `ui-install` `ui-lint` `ui-test` `ui-build` `ui-licenses`; `.gitignore` `ui_src/node_modules/`, `src/corpus_kb/ui_dist/`), `.github/workflows/ci.yml` (new `ui` job, ubuntu-latest: setup-node 24 + pnpm installed directly, `poe ui-install` (frozen lockfile), `ui-lint`, `ui-test`, `ui-build`, `ui-licenses` stubbed pass, upload `ui_dist` artifact), `docs/research/harness-zero-drift-program.md` map rows for the new gates
- Delete (same PR, second commit, after the mount works): `src/corpus_kb/ui/{app,pages,view_models,advisor_vm,demo_kb_vm,render,types,health,settings}.py` (the `ui/` package keeps only `__init__.py` + `spa.py`), `tests/test_ui_viewmodels.py`, `tests/test_ui_advisor.py`, `tests/test_ui_settings.py`, `tests/test_demo_kb_view.py`, `tests/test_ui_wiring.py` (superseded by `test_ui_spa_mount.py`). The `demo_kb` tool itself STAYS — the investor demo seeds from it.
- Test: `tests/test_ui_spa_mount.py`

**Interfaces (produced):**
- `corpus_kb.ui.spa.mount_spa(app: FastAPI, config: dict, dist: Path = DIST_DIR) -> bool`; route `GET /app/runtime-config.json` (serves the `ui.spa` block + `server_version`, `Cache-Control: no-cache` on index.html) registered before `Mount("/app", StaticFiles(html=True))`; when the dist exists, `GET /` → 307 `/app/`. No `frontend_choice` — the SPA is the only UI.

**Steps:**
- [ ] Failing tests: dist present → `/app/` 200 + runtime-config matches; dist missing → one `[ui] SPA build not found - run: poe ui-build` log, API still 200; `GET /` → 307 `/app/` when dist exists; post-deletion: no NiceGUI routes anywhere, `src/corpus_kb/ui/` holds only `__init__.py` + `spa.py`, and the suite is green with the `[ui]` extra absent (CI installs no extras)
- [ ] Implement per spec D.1 + wiring; run tests green
- [ ] Scaffold `ui_src/`; `poe ui-install && poe ui-lint && poe ui-test && poe ui-build` green locally; `ui_dist/index.html` exists
- [ ] Bundle probe BEFORE any screen work: throwaway route using Layout+Table+Form+Select+Upload+Switch; record its gzip size in the PR and set the bundle cap from that data (Task 9's 400 KB is provisional until then). Same PR records one line on why Vite 8.3.x is safe for this build (rolldown 1.0 stable since May 2026; patch line current).
- [ ] Full `poe verify` + pytest green, zero new warnings; two commits: `feat: mount lifecycle spa scaffold at /app with build gates`, then `chore: remove legacy nicegui ui`
- [ ] Push PR-1; poll CI every 20 min until all workflows green

### Task 4: Op manifest + API client + hooks + shared components (PR-2) — 1 builder

**Files:**
- Create: `scripts/gen_ui_manifest.py` (spec D.2: load `surface_registry` by path, merge `research_http.tool_routes()`, sorted JSON, `--check` mode; ALSO emits `ui_src/src/api/ops.generated.ts` — the typed op table `id → {path, method, longRunning}` with an `@generated` header that `ui-lint` refuses to let humans edit — and `ui_src/src/api/thresholds.generated.ts` from the `config.py` policy defaults (badge thresholds, compile-time, no runtime fetch), `src/corpus_kb/ui_manifest.json` (generated, committed), `tests/test_ui_manifest.py`, `ui_src/src/api/{client,errors,types,ops}.ts` (client = spec D.3 verbatim behavior; `ops.ts` re-exports the generated table; payload interfaces live only next to the screen that owns the op), `ui_src/src/api/manifest.json` (copied by `poe ui-build` pre-step — add `node scripts/copy-manifest.mjs`), `ui_src/src/hooks/{useOp,useLongOp}.ts` (D.5), `ui_src/src/components/{StatusBanner,ErrorPanel,UnavailablePanel,ReceiptCard,JsonViewer,OpRunner,ConfirmGate,CitedAnswer,DocumentsTable,TopicsTable}.tsx`, `ui_src/src/test/` (vitest setup + recorded fixtures via `ui_src/scripts/record-fixtures.ts` — fixtures are recorded from real envelope responses so payload drift fails `ui-test`), `tests/test_ui_strings_ste.py` (reads `en.json`, reuses the existing `poe ste` helper), `ui_src/src/strings/en.json`
- Modify: `pyproject.toml` (`ui-manifest` wired into `poe gen`; `ui-licenses` real: `pnpm licenses list --prod --json` → allowlist check → `ui_licenses.lock.json` committed), `.github/workflows/ci.yml` (ui job gains `poe ui-manifest --check` — without this the gate exists locally only, violating zero-drift rule 10)

**Interfaces (produced — later tasks consume exactly these):**
- `callOp<T>(path, {method?, body?, signal?, timeoutMs?: number | "none"}): Promise<OpResult<T>>` with `OpResult{ok, kind: "ok"|"error"|"unavailable"|"blocked"|"network"|"unknown", httpStatus, status, message, remediation, errorType, data}` (spec D.3)
- `verifyBadge(verify, thresholds)` — thresholds imported from `thresholds.generated.ts` (codegen from `config.py`, see Global Constraints), spec D.4 rows; `useLongOp(key, fn)` with `{running, elapsedSec, unknownOutcome}` (D.5), generic `<OpRunner opId={string}/>` driven by `manifest.json` (`paramsKnown: false` → validated JSON editor)
- Manifest entry shape = spec D.2 (`longRunning` flag list as given there)

**Steps:**
- [ ] Spike T0.1 first (2 min): start the server, count `/openapi.json` paths. If it lists `/api/*` with schemas, STOP and switch types-generation to `openapi-typescript` (record in PR); otherwise proceed with the manifest. Expected: plain Starlette `Route`s are absent → manifest stands.
- [ ] Failing test for the generator first (fresh generation == committed file, JSON **and** generated TS; every manifest path exists in `create_http_app()`); implement; `poe gen` leaves no diff
- [ ] Client table tests (spec D.3 list: failure envelope, 200-with-error, 503, 409, empty/degraded-on-200, non-JSON, network, timeout) + verifyBadge rows (incl. the missing-thresholds degrade path) + useLongOp (double-start refused, beforeunload, unknown-outcome) + SSE reader NOT included (Program 2)
- [ ] Form coverage, reported in the PR: count of ops with real typed forms vs JSON-editor fallback; floor = every `longRunning` op and every screen-owned op gets a typed form (the OpRunner JSON editor is the fallback, not the default — "controls the whole corpus without the CLI" is the DoD). Every recorded fixture validates against its declared TS type (typeguard-style assertion), so response-shape drift fails `ui-test`.
- [ ] STE test for `en.json` green; `poe ui-licenses` writes lockfile; commit `feat: op manifest and typed api client with shared components`; PR-2 green

### Task 5a: Demo-critical routes (UNBLOCKED — additive, touches no cli-reorg files) — 1 builder

The demo slice cannot seed without these; do not let them wait on Task 5b. All additive thin adapters, registry-first, same envelope taxonomy.

- [ ] `POST /api/demo/load` — expose the existing `demo_kb` tool (verified: no HTTP route exists today); idempotent, plain-English receipt
- [ ] `POST /api/ingest/upload` — B3 upload (adds `python-multipart` to `[ui]`-adjacent deps; Apache-2.0 verify; size cap `ui.spa.max_upload_mb`)
- [ ] `GET /api/doctor` — B4 read-only diagnostics shape (the Setup screen consumes it; on stage it hides behind "System ready")
- [ ] `scripts/gen_demo_corpus.py` — deterministic synthetic generator (100–300 docs derived from the demo-kb patterns; committed output or seeded-on-load; STE-clean text). Scale proof for the demo.
- [ ] Parity catalogue + map rows updated first; `poe verify` + pytest green; commit `feat: add demo load upload and doctor routes`

### Task 5b: B2–B4 remaining backend routes (PR-3) — BLOCKED on cli-reorg merge; 1 builder

**Files:**
- Modify: `src/corpus_kb/surface_registry.py`, `src/corpus_kb/surface_registry_parity.py`, `tests/test_surface_parity_deep.py` FIRST (registry-first rule); then route modules + tools
- Create routes (all four surfaces where a tool exists; HTTP adapters via `ToolRoute`): `GET /api/doctor` (reuses `ui/health.py` probes + embedding-dimension row; 503 only if probes cannot run), `POST /api/ingest/upload` (multipart `file`, opt `source_type`; temp file → existing `ingest_file` tool → result unchanged; cap `ui.spa.max_upload_mb` → taxonomy 413; basename-sanitize; `python-multipart` added after license check — Apache-2.0, verify), `GET /api/projects`, `GET /api/codebooks`, `GET /api/codebooks/versions`, `GET /api/codebook/proposals`, `GET /api/coding/assignments` (filters `review_status`, `code`, `limit`, `offset`), `GET /api/ingest/runs?project_id=`
- Tests: route tests with `conftest.pg_pool` fakes + parity rows + STE strings

**Constraint:** read-only projections; if any read needs a new table or column, STOP and ask the owner (spec B2). Deterministic ordering with tiebreakers. `poe gen` after registry rows.

**Steps:**
- [ ] Rebase `feature/ui-spa` on master (post-cli-reorg); resolve `pyproject.toml`/`AGENTS.md` overlaps
- [ ] Registry + parity rows → failing route tests → adapters → `poe verify` + `poe gen` green → commit `feat: add lifecycle read, doctor, and upload routes`; PR-3 green

### Task 6: Screens — Setup, Ingest, Review (PR-4a) and Scale, Ask (PR-4b) — 2 parallel builders, disjoint pages

Builder A owns `ui_src/src/pages/{Setup,Ingest,Review}/`; builder B owns `ui_src/src/pages/{Scale,Ask}/`. Shared: only Task 4 components (gaps → report, don't edit the other's files).

**Demo slice first (investor draft milestone):** within this task, land the money path before everything else, in order: **Home** (builder A: corpus summary + next-step card when documents exist — documents-exist check routes the landing; stepper otherwise) → Ingest "Load sample knowledge" (Task 5a route) → Ingest Files drag-drop (Task 5a upload — the live transformation beat) → Ask with cited answer + hover-citation signature interaction + verify badge + elapsed-time via `useLongOp` → one honestly-badged "not verified" answer → topics bar-list + one analysis → method advisor card (pulled forward from Task 7 if the demo date demands). That state is showable to investors (10-minute script pinned here per the Program goal section); the remaining pages complete the task.

**Steps (each page per spec F3–F7; Appendix F parity is void — NiceGUI is gone):**
- [ ] Setup: doctor table (B4), dims < 1024 warning (spec Appendix E string), feature-flags panel with exact config keys
- [ ] Ingest tabs: Files (upload → B3; client-side max check; ≤ 2 concurrent; receipt rows), Website (`ingest/url` + crawl toggle), Paste text, Server path (`ingest/directory`, `research/ingest`), Transcripts, Sample data ("Load sample knowledge" — confirm HTTP route in Task 4's manifest; if absent, expose `demo-kb` op through Task 5 rules)
- [ ] Review: documents table (`GET /api/documents` with limit; tags/metadata panels; delete behind ConfirmGate typing the title), degraded-chunks banner + re-embed, coding review queue (Task 5 assignments; accept/override → `review/execute`, override requires note), topics table
- [ ] Scale: run ledger polling `ingest/status` at `ui.spa.poll_ms` (pause when tab hidden; back off 10 s on 503), bulk folder ingest + `force` toggle warning, re-embed watch
- [ ] Ask: `POST /api/ask` cited sentences + sources + `verifyBadge` + Appendix E exact strings for `empty`/`degraded`; tabs Search / Verify / Notebook ask (`retrieval_only` switch); history client-only (sessionStorage)
- [ ] Each page: loading skeleton, empty state with next-step button, error panel with remediation + "Copy diagnostic"; page-level vitest from recorded fixtures; commit per page group `feat: add setup and ingest screens` / `feat: add review screens` etc.; PR-4a and PR-4b stacked, each green

### Task 7: Screens — Analyze + OpRunner (PR-5a) and Codebook + Release (PR-5b) — 2 parallel builders

Builder A owns `pages/Analyze/`; builder B owns `pages/{Codebook,Release}/`.

**Steps (spec F8–F9):**
- [ ] Analyze: guided cards (topics, labeling, classification, analyze, backfill-dates, embed-meta, statements, conflicts, beliefs, extraction-tune); method advisor (`analysis/select-method`, "Run this method" pre-fills OpRunner); manifest-driven OpRunner for the 11 analysis methods; dormant features show the exact config key + copyable YAML — never edit config from the browser
- [ ] Codebook: pre-flight card (project, versions from Task 5, dims check); step cards for the coding pipeline ops; notebook evidence/uncoded/overlap; promote gate as ConfirmGate with typed name + definition (human-only, duplicate-block merge suggestion shown); governance report rendering; research cycle wizard `mode: "in"` one stage per click with exit-code mapping (0/2/10–17/20/21); `approve` never sent (explicit off-by-default control only)
- [ ] Release: assess / request / review / execute / status / compare; HTTP 409 `blocked` rendered as structured refusal listing failing gates; ConfirmGate (approver + gate summary + waivers) before execute
- [ ] Vitest per screen from fixtures; commits + green stacked PRs

### Task 8: Investor-demo walkthrough (no PR; gate for Tasks 6–7)

- [ ] Run the 10-minute demo script live against a fresh server on preloaded demo-kb + synthetic corpus: every step works, zero error panels, honest badges on any degraded state, timings recorded per step; log any gap as a fix commit on the newest PR
- [ ] Demo-day risk drill once: kill Ollama mid-demo (Ask degrades honestly), shrink the window to 1024 wide mid-demo (layout holds), server restart (SPA reloads via the version handshake) — each shows a plain-English state, never a stack trace
- [ ] Demo hygiene: warm-up run completed (embeddings + one generation) and timed BEFORE the room fills; screen-recorded backup of the full demo saved alongside the runbook; demo-pass string audit clean (no config keys, exit codes, or status-enum words on demoed screens)

### Task 9: Hardening + docs (PR-6) — 1 builder

**Files:**
- Create: `ui_src/e2e/` (Playwright smoke: server on throwaway DB → sample ingest → ask accepts degraded → advisor → promote gate renders; axe-core a11y scan — devDependency, MPL exempt from prod gate), `ui_src/scripts/check-bundle.mjs` (gzip total of initial route ≤ 400 KB, wired into `poe ui-build`), `docs/UI-SPA.md`, `docs/UI-DEVELOPMENT.md` (incl. spec §14 runbook + spike results T0.1–T0.4, T0.6 — T0.5/T0.7/T0.8 move to Program 2)
- Modify: `docs/UI.md` (SPA only — all NiceGUI content removed), `docs/ADMIN.md` (new keys), `README.md`, `CHANGELOG.md`, `AGENTS.md` (final gate lines if not done in Task 2), `.github/workflows/ci.yml` (`ui-e2e` job REQUIRED from this PR — a skippable gate is not a gate, rule 10; it degrades without Ollama exactly as the pytest suite does)

**Steps:**
- [ ] Bundle-budget gate + e2e + a11y green; Vale green on all docs; `poe verify` + `poe ui-test` + `poe ui-build` zero warnings
- [ ] If the bundle gate fails, in order: drop the heaviest antd usage, then vendor-split chunks; raising the 400 KB cap needs owner sign-off with a written reason. Never delete the check.
- [ ] Manual acceptance (spec §11): demo corpus lifecycle, 100-file folder, server stopped mid-run, Ollama stopped; commit `feat: harden ui spa with e2e, budget gate, and docs`; PR-6 green

### Tasks 10–11: VOID (D9 closed 2026-10-25)

The owner ruled NiceGUI dies at program start. The former "owner flip" and "decommission + soak" tasks no longer exist — deletion happens inside PR-1 (Task 3), so the server never has zero UI and the tree never has two.

## Definition of done (Program 1)

Spec §18 minus the three Assistant bullets. A new user can open `/app/`, load sample data, ask a cited question, run topics and one analysis method, run the deductive coding pipeline, review assignments, promote a code, and assess a release without the CLI. No existing surface behavior changed; human gates cannot be bypassed; all gates in poe + CI + map + AGENTS.md; docs and runbook merged. The investor draft arrives earlier: after Task 6's demo slice, a 10-minute scripted demo on seeded demo-kb data runs clean (verified live in Task 8, including the degraded-mode risk drill).

**Closing condition (D9 closed 2026-10-25):** NiceGUI is deleted inside PR-1 together with the SPA mount — one UI in the tree from the first PR onward. No flip task, no soak, no dual-UI window.

**End-state maintainability scorecard (measured 2026-10-25; re-measure at Task 9 and Task 11, commit in `docs/UI-SPA.md`):**

| Axis | Today (NiceGUI) | End state (SPA + Task 11) |
|---|---|---|
| UI under CI | Never — `[ui]` extra not installed in CI; 1 real test always skipped | 5 gates, ubuntu CI job, generated-artifact diff |
| Browser tests | None | Playwright smoke + axe-core |
| Coupling | 13 internal modules imported in-process; shared asyncpg pool in view layer | Zero internal Python imports; HTTP envelope only |
| Business logic in UI | ~200 LOC (threshold 0.99, error mapping, temp-file lifecycle, result capping) | None; policy constants server-fed via runtime-config |
| Policy constants | 1 source, but lives in a file scheduled for deletion | 1 source (`config.py`), consumed by runtime-config |
| Change feedback loop | Edit server Python, restart process; no HMR | Vite HMR; server untouched for UI-only changes |
| Runtime deps | nicegui 3.18 (8.5 MB wheel, ~25 transitives) | None added to pip; Node is build-time only |
| LOC | 1,046 UI + 959 tests | ~8,000 budgeted TS (gated per PR) — honest 7× growth, justified by the rows above |
