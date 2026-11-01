# Browser UI

The browser UI is a React single-page app (SPA). The server serves it at
`/app/` on the usual HTTP port. There is no new daemon and no new port.
The lifecycle app shows one page per stage, in order: Setup, Ingest,
Review, Scale, Ask, Analyze, Codebook, Release. The stage order is the
research flow: set up, load data, review, scale up, ask, analyze, code,
release. This page covers the screens and what each one promises. The
demo script is [research/UI-DEMO.md](research/UI-DEMO.md).

## Contents

- [Start the app](#start-the-app)
- [User guide](#user-guide)
- [Config](#config)
- [Build and gates](#build-and-gates)
- [Layout](#layout)
- [The e2e recipe](#the-e2e-recipe)
- [Conventions](#conventions)
- [Troubleshooting](#troubleshooting)
- [Maintainability scorecard](#maintainability-scorecard)
- [Spike results](#spike-results)

## Start the app

```bash
corpus-kb start
```

Open `http://localhost:8010/app/`. The server opens your browser at
startup unless `CORPUS_KB_UI_OPEN=0` is set.

Without a build the server logs one line and keeps the API working:

```text
[ui] SPA build not found - run: poe ui-build
```

## User guide

### Setup

The page checks the system on its own: the database, the model server,
and the environment variables. Every check row shows its fix when it
fails. The sample-knowledge card loads five short files through the
standard pipeline; it is the fastest way to fill the library.

### Ingest

One tab per source. Files upload from your machine (a per-file size cap
comes from the server config). Website fetches one address, or crawls
the whole site with the toggle. Server path walks one folder on the
server, with subfolders on or off. The same tab runs the research
ingest, with its force warning. Transcripts run one transcript file.
Sample data re-runs the sample load; it skips files it already has.

### Review

The degraded-chunks card counts chunks without vectors and re-embeds
them. The review queue lists the model's code assignments; Accept
confirms one, Override replaces it and needs a note. Your reviewer
name stays in this browser. The topics table lists the built topics.

### Scale

The run ledger polls on its own while the tab is open. It pauses when
you hide the tab and backs off to ten seconds after a server error.
The bulk cards load one folder, or one research path with the force
toggle. Force re-ingests files the ledger already has.

### Ask

One question in, a cited answer out. Every answer sentence names its
sources; hover a citation to see its linked sentences. The badge is
honest: verified, partial, or not verified. A degraded answer shows the
source text without generation, plus the reason. Search, Verify, and
Notebook ask sit on their own tabs. Recent questions stay in this
browser session only.

### Analyze

The method advisor ranks the register's methods for your task, with
each row's validation and claims guidance. "Run this method" pre-fills
the runner. The guided cards run the build steps. A feature that is
off shows its config key and a copyable YAML snippet. The app never
edits the config.

### Codebook

The pre-flight shows the projects, the codebook versions, and the
embedding check. The coding steps run behind confirm gates. The
promote gate is human-only. You type the name and the definition. A
duplicate block names the merge suggestion. The notebook views show
evidence, uncoded units, and overlap. The governance report renders
its traffic lights and next actions. The cycle wizard runs one stage
per click and never approves for you.

### Release

Assess composes every gate over the current evidence and writes
nothing. Request opens the draft; review shows the packet and the next
action; status shows the stage. Execute needs an approver and shows
the gate summary before it runs. A blocked release names its unmet
required gates — that is the remediation, not an error. Compare diffs
two releases.

### Config keys the app reads

The app reads `ui.spa.poll_ms`, `ui.spa.request_timeout_s`, and
`ui.spa.max_upload_mb` from `/app/runtime-config.json` at boot. See
[ADMIN.md](ADMIN.md) for the values and bounds.

## Config

```yaml
ui:
  spa:
    poll_ms: 2000
    request_timeout_s: 60
    max_upload_mb: 50
```

Each key has a plain-English rule, and a bad value refuses to load the
config. The block reaches the app as `/app/runtime-config.json`. The
key list is closed: unknown keys never travel to the browser.

## Build and gates

The SPA is built from `ui_src/` by Vite into `src/corpus_kb/ui_dist/`.
Node 24 and pnpm are build-time tools only. The runtime stays pure
Python, and no Node output runs on the server.

```bash
poe ui-install   # pnpm install --frozen-lockfile
poe ui-lint      # ESLint + tsc --noEmit (strict)
poe ui-test      # vitest
poe ui-build     # vite build + the bundle budget gate
poe ui-e2e       # Playwright smoke + axe on a live throwaway server
poe ui-bundle    # the budget gate alone
poe ui-manifest  # ops-table drift check (also in poe gen)
poe ui-licenses  # production license allowlist
```

CI runs the same targets: the `ui` job for the build gates, the
REQUIRED `ui-e2e` job for the live smoke. The e2e job needs Postgres
(the bundled image) and degrades without Ollama exactly like the pytest
suite. The gates cover lint, types, unit tests, the build, the bundle
budget (350 kB gzip per initial route), the license allowlist, and the
manifest drift check. The Playwright smoke runs with the axe scan. The
CI enforcement map in
[research/harness-zero-drift-program.md](research/harness-zero-drift-program.md)
(rule 10) holds the pairings.

## Layout

- `ui_src/src/` — the app: `api/` (client, ops table, runtime config),
  `components/` (shared cards), `pages/<Stage>/` (one folder per
  stage), `strings/en.json` (every user-visible word).
- `ui_src/e2e/` — the Playwright smoke and the axe scan.
- `ui_src/scripts/` — the bundle gate and the manifest copy.
- `src/corpus_kb/ui_dist/` — the build output the server serves.

The ops table (`ui_src/src/api/ops.generated.ts`) is generated from the
server registry; screens never hardcode a route.

## The e2e recipe

`scripts/ui_e2e.py` is the one entry point. It creates a throwaway
database and starts `corpus-kb` against it. It waits for `/app/`,
preloads `POST /api/demo/load`, runs Playwright, then tears both down.
The database name comes from `CORPUS_KB_TEST_DB`.

First-time setup: `pnpm -C ui_src exec playwright install chromium`.
You need the venv console script, pnpm on PATH, and a reachable
Postgres. Ollama is optional. A failed pull degrades the demo load to
zero vectors; the ask accepts the honest degraded answer.

## Conventions

- Every user-visible word lives in `strings/en.json`; the interface in
  `strings/index.ts` pins the shape. Plain English, short sentences.
- New ops come from the manifest (`ops.generated.ts`); screens gate
  missing ops behind honest not-wired banners that self-upgrade.
- Long-running ops run behind confirm gates with elapsed time, cancel,
  and check-status. Destructive runs sit behind typed confirm gates.
- One shared card per op (PathCard, ActionCard, IdFormCard, DegradedCard);
  stages compose cards and never copy a pipeline.
- Files stay under 250 lines; `any` and `@ts-ignore` are hard errors.
- Playwright artifacts (`test-results/`, `playwright-report/`) are
  git-ignored.

## Troubleshooting

| Symptom | Likely cause | Check | Fix |
|---|---|---|---|
| `/app/` returns 404 | `ui_dist` missing | server log; `ls src/corpus_kb/ui_dist` | `poe ui-install && poe ui-build` |
| Blank page, console 404 on JS files | Vite `base` not `/app/` | `ui_src/vite.config.ts` | Set `base: "/app/"`, rebuild |
| Dev server calls fail with CORS errors | Calling `:8010` from `:5173` | browser console | Use the Vite proxy; do not edit server CORS |
| Every call shows "The server did not answer" | Server down, wrong port | `corpus-kb doctor` | `corpus-kb start --transport http --port 8010` |
| Ask returns a degraded answer | Generation or Ollama unavailable | `corpus-kb doctor` | Start Ollama, pull the configured model. The answer shows sources without generation — that is designed |
| Analysis card shows "disabled" | Feature flag off | the card shows the config key and remedy | Set the key in `config.yaml`, restart |
| Missing extra (`topics`, `latechunk`) | Optional extra not installed | the card shows the remedy | `pip install 'corpus-kb[topics]'` |
| Research screens say embeddings unavailable | Embedder down, or dims under 1024 | Setup screen | Pull `qwen3-embedding:8b-q8_0`; set `embedding.model` and `embedding.dimensions` |
| Upload fails with a size error | `ui.spa.max_upload_mb` too low | docs/ADMIN.md | Raise the key or ingest by folder path |
| Long operation seems stuck | Synchronous work still running | "Check status" polls the ledger | Wait; do not resubmit |
| Release execute says Blocked | Required gates fail without a waiver | the refusal lists them by name | Fix the gates or waive with a justification |
| Build fails on the license gate | New dependency with a disallowed license | `ui_licenses.lock.json` diff | Replace the dependency |
| `poe gen` leaves a diff in CI | Manifest or harness pack stale | CI diff | Run `poe gen`, commit |
| `pytest -m ste` fails on UI strings | Sentence too long or hard word | test output names the key | Shorten the string in `en.json` |
| Bundle gate fails | A chunk or the initial route over budget | the gate prints per-chunk kB | Drop the heaviest usage or split chunks; never delete the check |

## Maintainability scorecard

Re-measured 2026-10-27 (Task 9):

| Axis | Plan end state | Measured |
|---|---|---|
| UI under CI | 5+ gates, ubuntu CI job | Yes: ui job (lint, test, build, manifest, bundle, licenses) plus the REQUIRED ui-e2e job |
| Browser tests | Playwright smoke + axe-core | Yes: ui_src/e2e (8 checks, axe on 3 stages) |
| Coupling | Zero internal Python imports; HTTP envelope only | Yes: the SPA calls HTTP only; scripts/ui_e2e.py is the one bridge |
| Business logic in UI | None; policy constants server-fed | Yes: badge thresholds ship from config (thresholds.generated.ts); runtime knobs via runtime-config |

## Spike results

Recorded verdicts for the plan's Phase 0 spikes. T0.5, T0.7, and T0.8
(assistant spikes) moved to Program 2.

- **T0.1 OpenAPI types** — rejected. The server registry is the one
  contract. The SPA ships the generated ops table (`ops.generated.ts`)
  and the manifest, not generated OpenAPI types. The manifest drift
  check pins this every PR.
- **T0.2 Mount order** — verified. The SPA mounts at `/app/` before the
  API catch-all; `/api/*` and `/app/*` both answer on one port. Pinned
  every run by the e2e smoke, which loads `/app/` and calls `/api/*`.
- **T0.3 Tab close during a long POST** — the v1 contract is a
  synchronous POST plus ledger polling. The UI copy promises nothing.
  The stop-wait is honest: it shows the unknown-outcome state and
  offers "Check status" instead of a retry.
- **T0.4 Browser limits** — v1 has no streaming, so the SSE proxy
  risks do not apply. Long ops wait with no cap; other calls use the
  runtime cap. The e2e long-op runs verified this on the real server.
- **T0.6 Inventory of read tools** — the audit drove Task 5b. The
  lifecycle read routes now cover projects, codebooks, proposals, and
  coding assignments; `ingest/status` covers the runs. The Review,
  Codebook, and Scale screens read them.
