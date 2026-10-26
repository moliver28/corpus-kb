# Browser UI

The browser UI is a React single-page app. The server serves it at
`/app/` on the usual HTTP port. There is no new daemon and no new port.

## Build

The SPA is built from `ui_src/` by Vite into `src/corpus_kb/ui_dist/`.
Node 24 and pnpm are build-time tools only. The runtime stays pure
Python, and no Node output runs on the server.

```bash
poe ui-install
poe ui-build
```

CI runs the same targets in its `ui` job. The build gates cover lint,
types, unit tests, and the bundle budget (350 kB gzip per initial
route).

## Start

```bash
corpus-kb start
```

Open `http://localhost:8010/app/`. The server opens your browser at
startup unless `CORPUS_KB_UI_OPEN=0` is set.

## Honest degradation

Without a build the server logs one line and keeps the API working:

```text
[ui] SPA build not found - run: poe ui-build
```

## Stages

The app walks through eight lifecycle stages: Setup, Ingest, Review,
Scale, Ask, Analyze, Codebook, and Release. The app shell and the
Setup stage are live now. The remaining screens land task by task
([build plan](ui-spa-program-plan.md)).

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
