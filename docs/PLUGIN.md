# Corpus-Rag Plugin

The corpus-rag plugin adds Corpus-KB to Claude Code. It adds study commands, skills, and hooks.
This repository hosts the engine and the plugin. It is also the plugin marketplace. The plugin
lives at `plugins/corpus-rag/`. The marketplace manifest is `.claude-plugin/marketplace.json`.

## Requirements

- Claude Code.
- Python 3.11 or newer.
- `git` and `uv` on your PATH.
- Ollama with the `nomic-embed-text` model. See [INSTALL.md](INSTALL.md) for the engine install.

## Step 1: Add the marketplace

```bash
claude plugin marketplace add moliver28/corpus-kb
```

The command clones this repository. It reads `.claude-plugin/marketplace.json` and registers the
marketplace under the name `corpus-kb`.

## Step 2: Install the plugin

```bash
claude plugin install corpus-rag@corpus-kb
```

Restart Claude Code after the install.

## Step 3: Install the engine

The plugin drives the engine. It does not ship the engine. Install the engine before your first
study session:

```bash
corpus-rag engine
```

The command clones this repository at the release tag into `~/.corpus-kb/engine/<version>/`. It
installs the locked dependencies there. The engine then matches your plugin version. Use
`corpus-rag engine --force` to install the same version again.

## Step 4: First run

Run these commands in this order. They set up a study and prove the install works.

```bash
corpus-rag setup --questions
corpus-rag provision <slug>
corpus-rag ingest <file> -p <slug>
corpus-rag status -p <slug>
corpus-rag search "<query>" -p <slug>
corpus-rag op list_documents -p <slug>
corpus-rag backup <slug>
corpus-rag plugin-doctor
```

`plugin-doctor` prints `ok: true` when the plugin, the engine, and the state folder all agree.
A non-zero answer means a broken install. Fix it before you ingest real documents.

## Update

```bash
claude plugin update corpus-rag@corpus-kb
corpus-rag engine
```

Then restart Claude Code. The update installs the new plugin version. The `corpus-rag engine`
command installs the matching engine version beside the old one. Older engine folders stay on
disk, so you can roll back.

## Notes

- The install needs this repository to be public. It is public.
- The plugin keeps its marks and study list in `~/.corpus-kb/`. An update keeps that data.
- One tag, `corpus-rag-v<version>`, marks the engine commit for each plugin release.
- Engine install help: [INSTALL.md](INSTALL.md). Daily operation help: [OPERATIONS.md](OPERATIONS.md).
