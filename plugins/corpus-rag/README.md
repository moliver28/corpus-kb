# corpus-rag

The Corpus-KB plugin for Claude Code and ZCode. It connects your agent to a
local Corpus-KB engine: one database and one server per study, with a study
registry, per-study servers, and an op runner. Ingest, search, ask, migrate,
and back up each study by slug.

This plugin is built fresh in this repository. The repository is both the
engine and the plugin marketplace. One tag, `corpus-rag-v<version>`, marks the
engine commit for each plugin release.

## What it adds

- Skills: `corpus-ask`, `corpus-admin`, `corpus-analyze`, `corpus-ingest`.
- Commands: `/corpus-ask`, `/corpus-analyze`, `/corpus-ingest`.
- CLI: `corpus-rag` — `provision`, `list`, `use`, `serve`, `stop`, `ingest`,
  `search`, `similar`, `ask`, `sql`, `status`, `events`, `doctor`, `migrate`,
  `reembed`, `backup`, `ops`, `op`, `connect`, `raw-provision`, `engine install`.
- Hooks: small nudges that point at the right command at the right moment.

## Install

```bash
claude plugin marketplace add moliver28/corpus-kb
claude plugin install corpus-rag@corpus-kb
```

Then restart your agent. Full steps: [docs/PLUGIN.md](../../docs/PLUGIN.md).

## Engine connection

The plugin drives the `corpus-kb` CLI and MCP server. No engine yet? Install
one from the release tag (`--force` reinstalls and keeps the old folder):

```bash
corpus-rag engine install
```

Check the connection (per study once you have one):

```bash
corpus-rag status
```

Print an MCP config to paste into a client (one entry per study):

```bash
corpus-rag connect
```

`engine_paths.py` resolves the engine in this order: the `engine_root`
userConfig (`CLAUDE_PLUGIN_OPTION_ENGINE_ROOT`), `$CORPUS_KB_REPO`, a dev
checkout two folders up, then `<state>/engine`.

## Nudges

- Session start: if you work inside a `Research/<project>` folder with no
  registered study, the plugin suggests starting one. Once per project per
  day.
- Prompt: with a registered study, it may suggest `corpus-kb ask`,
  `corpus-kb analysis-select-method`, or `corpus-kb ingest` when your prompt
  matches. Once per lane per day.

To silence a lane for good, create a mark file in the plugin state folder:

```bash
mkdir -p ~/.corpus-kb/plugin/marks
touch ~/.corpus-kb/plugin/marks/declined-session   # or declined-ask, declined-analyze, declined-ingest
```

All hooks fail open. A broken hook never blocks your session.

## Development

```bash
pytest tests/            # stdlib-only suite (any Python 3.11+)
python scripts/plugin_manifest_gate.py   # from the repo root
```

From the repo root: `poe plugin-tests` runs both. The engine gates (ruff,
pyright, size ratchet, and friends) do not scan this plugin. It keeps its own
conventions and its own CI job.
