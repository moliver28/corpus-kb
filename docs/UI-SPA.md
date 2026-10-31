# The Corpus-KB browser UI (SPA)

The lifecycle app lives at `http://localhost:8010/app/` on the running
HTTP server. One page per stage, in order: Setup, Ingest, Review,
Scale, Ask, Analyze, Codebook, Release. The stage order is the research
flow: set up, load data, review, scale up, ask, analyze, code, release.

This guide covers the screens and what each one promises. The demo
script is docs/research/UI-DEMO.md. The build and gate details are in
docs/UI-DEVELOPMENT.md.

## Setup

The page checks the system on its own: the database, the model server,
and the environment variables. Every check row shows its fix when it
fails. The sample-knowledge card loads five short files through the
standard pipeline; it is the fastest way to fill the library.

## Ingest

One tab per source. Files upload from your machine (a per-file size cap
comes from the server config). Website fetches one address, or crawls
the whole site with the toggle. Server path walks one folder on the
server, with subfolders on or off. The same tab runs the research
ingest, with its force warning. Transcripts run one transcript file.
Sample data re-runs the sample load; it skips files it already has.

## Review

The degraded-chunks card counts chunks without vectors and re-embeds
them. The review queue lists the model's code assignments; Accept
confirms one, Override replaces it and needs a note. Your reviewer
name stays in this browser. The topics table lists the built topics.

## Scale

The run ledger polls on its own while the tab is open. It pauses when
you hide the tab and backs off to ten seconds after a server error.
The bulk cards load one folder, or one research path with the force
toggle. Force re-ingests files the ledger already has.

## Ask

One question in, a cited answer out. Every answer sentence names its
sources; hover a citation to see its linked sentences. The badge is
honest: verified, partial, or not verified. A degraded answer shows the
source text without generation, plus the reason. Search, Verify, and
Notebook ask sit on their own tabs. Recent questions stay in this
browser session only.

## Analyze

The method advisor ranks the register's methods for your task, with
each row's validation and claims guidance. "Run this method" pre-fills
the runner. The guided cards run the build steps. A feature that is off shows
its config key and a copyable YAML snippet. The app never edits the
config.

## Codebook
The pre-flight shows the projects, the codebook versions, and the
embedding check. The coding steps run behind confirm gates.
The promote gate is human-only. You type the name and the definition.
A duplicate block names the merge suggestion. The notebook views
show evidence, uncoded units, and overlap. The governance report
renders its traffic lights and next actions. The cycle wizard runs
one stage per click and never approves for you.

## Release

Assess composes every gate over the current evidence and writes
nothing. Request opens the draft; review shows the packet and the next
action; status shows the stage. Execute needs an approver and shows
the gate summary before it runs. A blocked release names its unmet
required gates — that is the remediation, not an error. Compare diffs
two releases.

## Config keys the app reads

The app reads `ui.spa.poll_ms`, `ui.spa.request_timeout_s`, and
`ui.spa.max_upload_mb` from `/app/runtime-config.json` at boot. See
docs/ADMIN.md for the values and bounds.

## Gates

CI runs these gates, and poe runs them locally.
The gates: lint, types, unit tests, the build, the bundle budget, the
license allowlist, and the manifest drift check.
The Playwright smoke runs with the axe scan.
The CI enforcement map in
docs/research/harness-zero-drift-program.md (rule 10) holds the pairings.

## Maintainability scorecard (re-measured 2026-10-27, Task 9)

| Axis | Plan end state | Measured |
|---|---|---|
| UI under CI | 5+ gates, ubuntu CI job | Yes: ui job (lint, test, build, manifest, bundle, licenses) plus the REQUIRED ui-e2e job |
| Browser tests | Playwright smoke + axe-core | Yes: ui_src/e2e (8 checks, axe on 3 stages) |
| Coupling | Zero internal Python imports; HTTP envelope only | Yes: the SPA calls HTTP only; scripts/ui_e2e.py is the one bridge |
| Business logic in UI | None; policy constants server-fed | Yes: badge thresholds ship from config (thresholds.generated.ts); runtime knobs via runtime-config |
