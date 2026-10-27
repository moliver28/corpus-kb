# UI development guide

How to build, test, and harden the lifecycle SPA (`ui_src/`). The user
guide is docs/UI-SPA.md; the demo script is docs/UI-DEMO.md.

## Layout

- `ui_src/src/` — the app: `api/` (client, ops table, runtime config),
  `components/` (shared cards), `pages/<Stage>/` (one folder per
  stage), `strings/en.json` (every user-visible word).
- `ui_src/e2e/` — the Playwright smoke and the axe scan.
- `ui_src/scripts/` — the bundle gate and the manifest copy.
- `src/corpus_kb/ui_dist/` — the build output the server serves.

The ops table (`ui_src/src/api/ops.generated.ts`) is generated from the
server registry; screens never hardcode a route.

## Build and gates

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
suite.

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

## Troubleshooting runbook (spec §14)

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

## Spike results (Phase 0)

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
