# Corpus-Rag Plugin

The corpus-rag plugin connects Claude Code or ZCode to a local Corpus-KB
engine. You register studies, ingest documents into them, then ask
questions and get answers with citations — one database and one server
per study. The plugin lives at `plugins/corpus-rag/` in this repository.
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

## Studies

A study is one slug with one database, one port, and one server. The
registry lives at `${CLAUDE_PLUGIN_DATA}/studies.json`, else
`~/.corpus-kb/plugin/studies.json`. A 0.6.0 `~/.corpus-kb/projects.json`
imports on first run; the old file stays.

Create one study (or adopt an existing database or file):

```bash
corpus-rag provision interview-study --backend postgres
```

Every command picks its study in this order: `-p <slug>` when you pass
it; the slug in the folder's `.claude/corpus.json` (searched upward);
the active study; else the command stops and asks you to name one. Set
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
| `status`, `connect`, `engine install` | Kept from 0.7.x; `status` is per study now, `connect` prints one MCP entry per study. |

### Servers and ports

Commands turn the study's server up when they need it, on the study's
port (first free port from `base_port`, stored in the registry). Health
probes run once a second for up to 90 s; a failed start prints the log
path (`<data>/logs/<slug>.log`) and its last lines. Every command marks
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

The password-bearing keys are marked sensitive; the plugin never prints
an unmasked DSN.

### Config overlay

The plugin passes a config overlay to every engine run through
`CORPUS_KB_CONFIG_OVERLAY`. It reads the owner's legacy
`~/.corpus-kb/config.overrides.yaml` when that file exists, otherwise
`<data>/config.overlay.yaml` when present. Write plain YAML there; the
plugin never parses it.

## Nudges

The plugin suggests the right command at the right moment.

- Session start: inside a `Research/<project>` folder with no registered
  study, it suggests starting one. Once per project per day.
- Prompt: with a registered study, it may suggest `ask`, `analysis`, or
  `ingest` when your prompt matches. Once per lane per day.

Each nudge is one short message. It never blocks your work. Every hook fails
open: a broken hook cannot stop your session.

To silence hints, create mark files in the plugin state folder
(`~/.corpus-kb/plugin/marks/`):

- `declined-session` — no more study hints.
- `declined-ask`, `declined-analyze`, `declined-ingest` — no more hints on
  that lane.

To mark a study as registered (stops the session hint), see the
corpus-ingest skill.

## ZCode notes

- ZCode loads plugin hooks from `hooks/hooks.json` and supports the events
  this plugin uses: `SessionStart` and `UserPromptSubmit`.
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
