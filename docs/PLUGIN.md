# Corpus-Rag Plugin

The corpus-rag plugin connects Claude Code or ZCode to a local Corpus-KB
engine. You register studies and ingest documents into them. Then you ask
questions and get answers with citations. Each study has one database
and one server. The plugin lives at `plugins/corpus-rag/` in this repository.
This repository is also the plugin marketplace. The manifest is
`.claude-plugin/marketplace.json`.

The plugin is built fresh in this repository. It has no older code inside.
One tag, `corpus-rag-v<version>`, marks the engine commit for each release.

## Requirements

- Claude Code or ZCode.
- A Corpus-KB engine install. See [INSTALL.md](INSTALL.md). You need Python
  3.11 or newer, and Ollama with the `nomic-embed-text` model.

## Step 1: Add the marketplace

Claude Code:

```bash
claude plugin marketplace add moliver28/corpus-kb
```

ZCode: add this repository as a marketplace in the ZCode plugin settings
(`marketplaces`), then sync. Restart your agent after you add it.

## Step 2: Install the plugin

```bash
claude plugin install corpus-rag@corpus-kb
```

Restart your agent after the install. ZCode reads the same plugin layout and
reloads it on restart.

## Step 3: Connect the engine

The plugin drives the `corpus-kb` CLI and MCP server. Check the connection:

```bash
corpus-rag status
```

This runs `corpus-kb doctor` and prints a short verdict. If it reports no
engine, finish the engine install first ([INSTALL.md](INSTALL.md)), or set
`CORPUS_KB_REPO` to your engine checkout.

To wire the engine into a client, print the MCP config and paste it:

```bash
corpus-rag connect
```

### Install the engine from a release tag

No engine yet? Install one into the plugin state folder:

```bash
corpus-rag engine install
```

This clones this repository at the tag `corpus-rag-v<plugin version>` into
`<state>/engine/`, then runs `uv sync --frozen --no-dev` there. The state
folder is `CORPUS_KB_STATE_DIR`, else `~/.corpus-kb`. Add `--force` to
reinstall. The old install then moves to
`<state>/engine-versions/<version>-<timestamp>/` for rollback. Nothing is
deleted. The command checks the tag before it moves anything. It never falls
back to another branch or tag. When the release tag does not exist yet, it
prints the nearest tags and stops. Wait for the release, or install the engine
by hand ([INSTALL.md](INSTALL.md)). A clone whose sync fails is removed, so
retries do not pile up broken copies. If a later step fails, the message says
where the old install is.

### Roll back to an older engine

Every install keeps the previous copy in `<state>/engine-versions/`. To roll
back, swap the folders:

```bash
cd ~/.corpus-kb    # or the folder in CORPUS_KB_STATE_DIR
mv engine engine-current
mv engine-versions/0.7.0-20261106T101530123456 engine
```

Then run `corpus-rag status` to check it. Delete `engine-current` when the
older version works for you.

## Three flows

### Ask with citations

You want an answer from one study's files.

```bash
corpus-rag ask "<your question>" -p <slug>
```

The answer arrives with citations. Use `/corpus-ask` in your agent, or run
the command yourself. If the question is vague, search first:

```bash
corpus-rag search "<topic words>" -p <slug>
```

### Pick an analysis

You have a research question but no method in mind. Use `/corpus-analyze`,
or run:

```bash
corpus-kb analysis-select-method
```

It names a method and says why it fits. The skill explains the choice in
plain English and offers to run it.

### Ingest documents

You want new files in one study's corpus. Use `/corpus-ingest`, or run:

```bash
corpus-rag ingest "<path or folder>" -p <slug>
corpus-rag op ingest_url -p <slug> url="<url>"
```

PDF, DOCX, PPTX, HTML, and text work directly. `.vtt` and `.srt` transcripts
ingest as text. Google Slides: export to `.pptx` first, then ingest.

### Code chunks in a study

You want to code the study's chunks against a codebook and measure how
well the coding holds up. The `codify` command group drives the engine's
coding ops. Use the three `/corpus-codify-*` skills for a guided walk:

```bash
corpus-rag codify provision interview-study --paradigm codebook
corpus-rag codify load-codebook codebook.json -p interview-study
corpus-rag codify pool -p interview-study
corpus-rag codify cells -p interview-study --coder gpt-x
corpus-rag codify code --cells cells.json -p interview-study --coder gpt-x
corpus-rag codify reliability -p interview-study
corpus-rag codify export -p interview-study --format csv
```

`codify provision` records the study's paradigm. `codify reliability`
refuses for reflexive studies before it calls the server: reflexive
thematic analysis rejects inter-coder reliability. The reliability
output carries the epistemological caution; the skills show the same
caution beside every alpha or kappa.

The event-sourced research pipeline runs through the op runner. It
covers themes, codebook promotion, deductive runs, review, reports,
and releases. The `/corpus-research` skill maps each stage to its op.

## Skills

The plugin ships nine skills. Each one walks an agent through one job
with the plain-English rules that job needs.

| Skill | Use it for |
|---|---|
| `corpus-ask` | Cited answers from one study's corpus. |
| `corpus-ingest` | Loading files, folders, and web pages into a study. |
| `corpus-admin` | Provision, migrate, re-embed, back up, events, doctor. |
| `corpus-analyze` | Picking the right analysis for a question. |
| `corpus-research` | The research pipeline: ingest, themes, promote, code, review, report, notebook, release. |
| `corpus-codify-study` | Starting a coding study: paradigm, provision, ingest, codebook. |
| `corpus-codify-code` | Running the coder: pool, cells, batches, floors, saturation, reliability. |
| `corpus-codify-export` | Reporting and exporting coding results with the caution. |
| `corpus-uxr` | The UX research study layer, from new transcripts to `themes.md`. |

The four coding references live in `plugins/corpus-rag/references/`:
codebook rules, coding rules, the epistemological caution, and
reliability and routing.

## Studies

A study is one slug with one database, one port, and one server. The
registry lives at `${CLAUDE_PLUGIN_DATA}/studies.json`, else
`~/.corpus-kb/plugin/studies.json`. A 0.6.0 `~/.corpus-kb/projects.json`
imports on first run; the old file stays.

Create one study (or adopt an existing database or file):

```bash
corpus-rag provision interview-study --backend postgres
```

Every command picks its study in this order:

- `-p <slug>` when you pass it.
- The slug in the folder's `.claude/corpus.json`, searched upward.
- The active study.

With no match, the command stops and asks you to name one. Set
the active study once per machine:

```bash
corpus-rag use interview-study
```

### Every command

| Command | What it does |
|---|---|
| `provision <slug> [--backend postgres\|duckdb]` | Create, migrate, and register a study. Adopts an existing database or file. Failure prints the cleanup command. `--project-id` keeps the 0.7.x raw-table behavior. |
| `raw-provision <slug> [--project-id X]` | The 0.7.x Track C raw table (renamed from `provision`). |
| `list` | Studies with database, port, up or down, active mark. |
| `use <slug>` | Set the active study. |
| `serve [-p]` / `stop [-p]` | Start or stop the study's server. |
| `ingest <path> [-p]` | Ingest one file or folder; counts land in the next `status`. |
| `search <query> [-p] [-k 5] [--mode hybrid\|context] [--context-chunks 2]` | Search; prints score, source, snippet, `chunk_id`. |
| `similar <chunk_id> [-p] [-k 5]` | Chunks nearest one stored chunk. |
| `ask "<question>" [-p]` | Cited answer from the study. |
| `sql "<SELECT>" [-p]` | Read-only SQL; the engine refuses writes. |
| `status [-p]` | Doctor verdict, counts, queue, Ollama line, hook errors, and what changed since the last status. |
| `events [-p]` | Event-store health. |
| `doctor [-p]` | Engine diagnostics with the study's database and overlay. Exit 3 means unreachable. |
| `migrate [<slug> \| --all] [--reset-embeddings]` | Pending migrations per study. |
| `reembed [-p] [--limit N]` | Rebuild missing vectors. |
| `backup <slug>` | `pg_dump -Fc` (excludes `ag_catalog`, runs as the superuser), or a DuckDB file copy. Prints the restore command. |
| `ops [--family F]` | The live op inventory with the live count. |
| `op <name> [-p] key=value ... [--json '{...}']` | Run any op; validated against the inventory first. Long ingest ops get a 7200 s timeout. |
| `codify provision <slug> --paradigm codebook\|reflexive` | Provision the study (when new) and record its paradigm in the registry. |
| `codify load-codebook <file.json> [-p]` | Validate, embed, and load one codebook; prints its version id. |
| `codify pool [-p] [--codebook-version ID]` | Pool keyword and vector candidates; defaults to the latest version. |
| `codify code --cells <file> [-p] [--coder] [--model] [--batch-id] [--coding-pass N] [--bands JSON]` | Code one batch of cells. The file holds a bare list or `{"cells": [...]}`. Bands accept only `high`, `mid`, `low`. |
| `codify reliability [-p] [--per-code-min 5] [--seed 17]` | Alpha and kappa reliability. Refuses for reflexive studies before any server call. |
| `codify calibrate-floors <labels.json> [-p]` | Calibrate one code's pool and residual floors from labeled chunks. |
| `codify saturation [-p] [--batch-id] [--threshold 0.05] [--min-samples 50]` | The saturation signal and the batch stop rule. |
| `codify status [-p]` | Coding corpus counts, coverage by disposition, routes. |
| `codify export [-p] [--format csv]` | Accepted codes joined with chunk text and document source. |
| `codify cells [-p] --coder <id> [--limit N] [--out cells.json]` | Build the next cells file: uncoded chunks with at most five candidate codes each, read-only SQL. |
| `setup --questions [--backend postgres\|duckdb]` | Preflight and the PII scan, then print the question payload as JSON. Exit 1 with fix commands on a hard preflight failure. Changes nothing. |
| `setup --apply --answers '<json>\|@file'` | Run the four journaled steps. A retry skips finished steps. Prints a verify list. |
| `setup --off [--all]` | Undo the allow rules and the autorun change, stop the server, set `enabled: false`, decline the offer. The database and the journal stay. `--all` covers every folder setup enabled. |
| `snooze [--days 7]` / `decline` | Hide the setup offer in this folder for N days, or for good. |
| `pause` / `resume` | Stop the server and set `enabled: false`, or remove the flag. Nothing else changes. |
| `signals` | Print the folder's signal-scan counts (files, transcripts, interview summaries). |
| `plugin-doctor [--bundle]` | Plugin health: versions, hook files, trigger file, folder state, this folder's last 10 hook errors. Exit 0 only when hooks and triggers are good. `--bundle` writes a redacted JSON file for a bug report. |
| `status`, `connect`, `engine install` | Kept from 0.7.x; `status` is per study now, `connect` prints one MCP entry per study. |
| `uxr consent [-p]` | Report the setup consent answer. Exit 1 means local only. Unknown means local only. |
| `uxr ingest-plan --spec UX1-N.spec.json [-p]` | Store the study and its question ids, keyed by a 16-char SHA-256 of the spec. Ids: RQn by position, Hn from the label prefix, An by position. |
| `uxr classify --dir <path> [--roster F] [--staff F] [-p]` | Sort files into `participant_session`, `internal_meeting`, `plan`, `derived`, `excluded`, `unmatched`. No model call. `unmatched` files go to the researcher; never guessed. |
| `uxr confirm-classes '<json>'\|@file [-p]` | Store confirmed classes. Only `participant_session` and `internal_meeting` reach the source list. |
| `uxr stubs --dir <path> [-p]` | Write `sources/corpus/<slug>.md` per source so `[S:slug]` cites resolve. Never overwrite. |
| `uxr check-quote --quote '<q>' (--unit-text '<t>'\|--unit-file F)` | `exact`, `near_miss`, or `fail`; exit 1 on fail. NFKC, lowercase, punctuation to space. Near miss: `difflib` ratio 0.90 or more on a sliding window. |
| `uxr validate-rows '<json>'\|@file [--store] [-p]` | Check stance enum, unit ids, evidence, and each quote. Failed rows store as `unverified`; near misses are logged. |
| `uxr rollup [--max-unverified 0.1] [--dir <path>] [-p]` | Count stances per question from verified rows; researcher lines from `interviews/*-summary.md` win. Exit 1 when the unverified share is over the limit. |
| `uxr contract-test [--evidence-file F] [--dir <path>] [-p]` | Compare the rollup with the report tool's `evidence-weight` output. Exit 1 on any difference. |
| `uxr write-themes --narrative-file F [--dir <path>] [-p]` | Write only between the `corpus:begin` and `corpus:end` markers in `analysis/themes.md`. Never opens `analysis/codes.md`. |
| `uxr apply-summary [--dir <path>] [-p]` | Write a marked block into each `interviews/<stem>-summary.md`. Skips question ids a researcher line covers. Byte-stable on a second run. |
| `uxr insights [--question ID] [--stance S] [--session S] [--format text\|json] [-p]` | Stored rows with rationale, checked quotes, unit ids, `[S:slug]` source, rollup, and ingested vs analyzed counts. |
| `uxr import [-p]` | One-time copy from a 0.6.0 `uxr` schema. Read-only on the engine side. |

### Servers and ports

Commands turn the study's server up when they need it. The port is
the first free port from `base_port`, stored in the registry. Health
probes run once a second for up to 90 s. A failed start prints the
log path (`<data>/logs/<slug>.log`) and its last lines. Every command marks
use; other studies' servers idle over 30 minutes stop, and the current
study's server never stops mid-command. On DuckDB studies the server
stops (and waits up to 30 s) before `migrate`, `reembed`, `doctor`, and
`backup`, because the file allows one writer.

### Settings (userConfig)

Claude Code prompts for these when the plugin is enabled; each exports
as `CLAUDE_PLUGIN_OPTION_<KEY>`:

| Key | Default | Used for |
|---|---|---|
| `engine_root` | empty (the resolution chain) | engine location |
| `app_dsn_template` | `postgresql://corpus_user:<password>@localhost:5432/{database}` | each study's DSN (`{database}` is replaced) |
| `superuser_dsn` | `postgresql:///postgres` (peer auth) | provision, migrate, backup |
| `base_port` | `8010` | port allocation |
| `default_backend` | `postgres` | provision |
| `uxr_report_tool` | empty | report tool command line for `uxr contract-test`; empty means use `--evidence-file` |

The password-bearing keys are marked sensitive; the plugin never prints
an unmasked DSN.

### Config overlay

The plugin passes a config overlay to every engine run through
`CORPUS_KB_CONFIG_OVERLAY`. It reads the owner's legacy
`~/.corpus-kb/config.overrides.yaml` when that file exists, otherwise
`<data>/config.overlay.yaml` when present. Write plain YAML there; the
plugin never parses it.

## Study folders and hooks

A study folder is a folder whose `.claude/corpus.json` names a registered
study. Inside one, the plugin works without being asked twice. The file
holds these keys:

| Key | Meaning |
|---|---|
| `slug` | The study slug. Required. |
| `watch` | Folders to scan, relative to the folder. Default: the folder itself. |
| `exclude` | Glob list that replaces the defaults (see below). |
| `auto_ingest` | Drain the queue on every command. Default true. |
| `enabled` | Set `false` to silence every hook in this folder. |
| `uxr.ticket` | Free-form note for the UXR layer. |

Default exclude globs: `interviews/participant-key.md`,
`**/raw-transcripts/**`, `analysis/**`, `deliverables/**`,
`**/*-summary.md`, `**/*-key-points.md`. The first two keep identity keys
and raw recordings out of the engine.

The hooks, one per event:

| Event | What it does |
|---|---|
| SessionStart (`startup\|compact\|clear`) | Prints the study block: slug, document count, last ingest date, the evidence rules, and the skill list. Returns `watchPaths` so file changes fire. Engine down: says `Run corpus-rag status`. |
| UserPromptSubmit | Queues files added outside a tool call (300 files, depth 4). Keeps the ask, analyze, and ingest lane nudges, keyed per study. In a research folder, runs the setup offer when the prompt matches the trigger list. |
| SubagentStart | Gives the subagent three lines: search first, the study slug, what counts as evidence. |
| PreToolUse (`Read\|Grep\|Bash`) | Nudges `corpus-rag search` when the agent reads a watched transcript by hand. Never blocks. 3 per session. |
| PostToolUse (`Write\|Edit`) | Queues the written file in a watch folder. |
| FileChanged | Queues the changed file; ignores `unlink`. |
| Stop | Logs a turn that read a transcript before any corpus call. Prints nothing. |

A transcript is a `.vtt` or `.srt` file. Or it is a file with
"transcript" in the name and a `.md`, `.txt`, or `.docx` suffix. Or it
is one of those files whose first 4096 bytes hold 3 or more speaker
labels with one repeated. Bold labels
(`**P1:** text`) parse like plain labels; the engine reads both, so no
normalized copy is made.

For five events (UserPromptSubmit, SubagentStart, PreToolUse,
PostToolUse, FileChanged) a shell guard runs before Python starts. It
walks up from the working folder for `.claude/corpus.json`. When the
file is absent or holds `"enabled": false`, the guard exits 0. UserPromptSubmit also passes when a
walk-up level holds the research sentinel, so the setup offer can run.
Hook cost outside study folders stays near zero. SessionStart always runs
(the daily prune), and Stop exits fast when no session record exists.
Every hook fails open: a broken hook cannot stop your session.

A folder with transcripts but no corpus.json gets the guided setup offer
(see the next section). To silence a prompt lane for good, create a mark
file in the plugin state folder (`~/.corpus-kb/plugin/marks/`):
`declined-ask`, `declined-analyze`, or `declined-ingest`. To decline the
setup offer in one folder, run `corpus-rag decline`.

## Guided folder setup

When you open a folder of interview transcripts, the plugin offers to
set a study up once per session. The offer fires on SessionStart
(`startup` only) and on UserPromptSubmit when the prompt matches the
trigger list in `hooks/triggers.json`. The list holds research nouns
plus analysis frames, minus exclusions such as "interview script" or
"embedding".
The offer text tells the agent to finish your current request first and
then ask one question. The choices: set up now, not now
(`corpus-rag snooze`, 7 days), or never here (`corpus-rag decline`).

The offer only fires under real conditions. The folder must hold no
`corpus.json`, must not be the engine repository, and must not be
declined or snoozed. The signal scan must find transcripts or
interview summaries. The scan runs at depth 2 inside a 0.6 s budget.
It skips hidden folders, `node_modules`, `__pycache__`, and `venv`.
After the first offer the plugin leaves a `.claude/corpus-research`
sentinel. It lets the prompt hook reach Python in this folder without
a scan. `decline` and `setup --off` remove it.

If you say yes, the agent runs the `setup` skill:

1. `corpus-rag setup --questions` — three checks, then the questions:

   - Preflight. Hard checks stop setup with fix commands. Ollama and
     the embedding model only warn.
   - A PII scan: per-file counts of email addresses and person-like
     names. It reports paths, kinds, and counts. It never reports
     text. Role labels are skipped.
   - The question payload (AskUserQuestion, four per call): study
     name, watch folders, backend, cloud-model consent, auto ingest,
     allow rules, queue autorun.
2. `corpus-rag setup --apply --answers '<json>'` — four journaled steps
   in `.claude/corpus.setup.json`:

   | Step | Does | Undo on `--off` |
   |---|---|---|
   | `database` | `corpus-rag provision <slug>` | never (data stays) |
   | `allow_rules` | Adds `Bash(corpus-rag search:*)`, `Bash(corpus-rag ask:*)`, `Bash(corpus-rag status:*)` (plus `Bash(uxr insights:*)` in a UXR study) to `.claude/settings.json` | removes them |
   | `engine_override` | Sets `queue.autorun: true` in the config overlay when chosen (global for all studies) | restores the prior overlay text |
   | `config` | Writes `.claude/corpus.json`, seed-ingests the smallest watched file, queues the rest | never |

   A failed step records `failed_step`; re-run `setup --apply` to resume
   (finished steps are skipped), or `setup --off` to undo.

The study slug comes from your answer, else the folder name. When
another folder owns that slug, six hex characters of the folder path's
SHA-1 are appended. A "No" on the cloud-model question stores
`local_only: true` in the gate state; no transcript text reaches a
cloud model.

The gate state lives at `${CLAUDE_PLUGIN_DATA}/gate-state.json` with one
entry per folder (`declined`, `snoozed_until`, `slug`,
`enabled_by_setup`, `local_only`), written atomically behind a lock. A
0.7.0 `study-registered` mark and old `declined-*` mark files are read
once, then removed. This happens when the gate state is first used, so
a prior "declined" choice carries over.

## The ingest queue

New and changed files in a watch folder queue in
`<folder>/.claude/corpus-pending.jsonl`. The queue holds one line per
path, skips repeats by path and mtime, and is guarded by a `.lock`. A transcript routes to
`research_ingest_transcript` and then `ingest_file`, so it reaches the
research tables and plain search. Other files route to `queue_enqueue`.

```bash
corpus-rag drain [-p <slug>] [--approve]
```

Drain sends each row, keeps failed rows, and drops rows for deleted files
with no engine call. Rows added while the drain runs stay queued. When
`auto_ingest` is false, only `--approve` drains. Every result lands in
`ingest-log/<slug>.jsonl` (ts, path, route, result).

Every `corpus-rag` command except `drain`, `list`, `provision`, `migrate`,
`backup`, and `stop` first drains the folder queue when `auto_ingest` is
on. `codify provision` counts as `provision` here. A drain failure never
blocks the command.

## UX research studies

The `uxr` command group and the `corpus-uxr` skill run one UX research
study, such as the tickets `UX1-275`. The layer ships as a module inside
corpus-rag (owner decision on issue #355), not a second plugin.

Three rules hold everywhere:

- No UXR code ever writes to a study database. The only engine reads go
  through the read-only `corpus-rag sql` route.
- Stance rows are model output. A row counts only after its quote checks
  against the unit text (`exact`, or `near_miss` at 0.90 or more). Rows
  that fail store as `unverified` and the rollup reports them.
- Analysis output lands only between `corpus:begin` and `corpus:end`
  markers. `write-themes` never opens `analysis/codes.md`.

Storage is one SQLite file per study, `uxr/<slug>.sqlite` under
`${CLAUDE_PLUGIN_DATA}`, with the tables `study`, `question`, `source`,
`session_insight`, and `schema_version`. A study joins to engine
documents by `doc_id`, read through `corpus-rag sql`.

A study counts as a UXR study when its `.claude/corpus.json` has
`uxr.ticket`. Or `00-overview.md` names a ticket, or a
`<PREFIX>-N.spec.json` file sits at the folder root. UXR studies get
three extras:

- SessionStart adds the UXR lines and the `corpus-uxr` skill to the
  study block.
- UserPromptSubmit nudges once per session when files sit in the queue
  (`corpus-rag drain`) or when ingested sessions have no stored analysis.
  A UXR phrase ("what did we learn", "is H1 supported") injects the
  `corpus-uxr` skill path once per session. A hook never runs the
  analysis.
- After `corpus-rag search`, up to three stored rows print under
  "Derived insights (model output, not evidence):".

The search line runs through a small generic extension point.
`post-search.json` under the plugin data folder lists commands that
run after every search. Each command gets the study slug and the
query. `corpus-rag` knows
nothing about the registered commands; `uxr post-search` is the one this
plugin ships. A command that fails or prints nothing stays silent.

`contract-test` compares the rollup with the report tool's
`evidence-weight` output. Set the tool command line in the
`uxr_report_tool` userConfig option, or pass `--evidence-file` with a
file that holds the JSON. Exit 1 means the report must not be written.

`consent` reads the setup answer from the gate state (issue #353).
`local_only` blocks every cloud-model path in the `corpus-uxr` skill: no
transcript text goes to a cloud model.

## Hook state files

State lives in `${CLAUDE_PLUGIN_DATA}` (or `~/.corpus-kb/plugin/`):

- `sessions/<id>.json` — one record per session (turn count, reads,
  nudge count, setup-offer flag), behind a `.lock` sidecar with an
  atomic write. Windows uses `msvcrt.locking`; other systems use `flock`.
- `gate-state.json` — the setup gate state, one entry per folder
  (issue #353), atomic write behind a lock.
- `post-search.json` — the post_search command registry (issue #355).
- `uxr/<slug>.sqlite` — one UXR store per study (issue #355).
- `hook-errors.jsonl` — one line per swallowed hook failure.
- `telemetry.jsonl` — one line per manual-read turn.
- `ingest-log/` — one JSONL per study, one row per drained file.
- `.pruned` — the last prune date. Once a day, SessionStart removes
  sessions older than 30 days and caps the two logs at 1 MB.

## ZCode notes

- ZCode loads plugin hooks from `hooks/hooks.json` and supports the events
  this plugin uses: `SessionStart`, `UserPromptSubmit`, `SubagentStart`,
  `PreToolUse`, `PostToolUse`, `FileChanged`, and `Stop`.
- Hook scripts stay POSIX `sh`, so the same files work on Windows, Linux,
  and macOS.
- Restart your agent after a plugin update; hooks and skills reload then.

## Development

```bash
poe plugin-tests      # from the repo root: plugin suite + gate
pytest tests/         # from plugins/corpus-rag: suite only
```

The engine gates (ruff, pyright, size ratchet) do not scan the plugin. The
plugin keeps its own tests, its own pytest config, and its own CI job.
