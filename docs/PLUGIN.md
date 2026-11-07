# Corpus-Rag Plugin

The corpus-rag plugin connects Claude Code or ZCode to a local Corpus-KB
engine. You ingest documents, then ask questions and get answers with
citations. The plugin lives at `plugins/corpus-rag/` in this repository. This
repository is also the plugin marketplace. The manifest is
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

You want an answer from your own files.

```bash
corpus-kb ask "<your question>"
```

The answer arrives with citations. Use `/corpus-ask` in your agent, or run
the command yourself. If the question is vague, search first:

```bash
corpus-kb search "<topic words>"
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

You want new files in the corpus. Use `/corpus-ingest`, or run:

```bash
corpus-kb ingest "<path or folder>"
corpus-kb ingest-url "<url>"
corpus-kb ingest-status
```

PDF, DOCX, PPTX, HTML, and text work directly. `.vtt` and `.srt` transcripts
ingest as text. Google Slides: export to `.pptx` first, then ingest.

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
