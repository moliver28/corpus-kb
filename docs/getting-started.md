# Getting started with Corpus-KB

Corpus-KB answers questions from your own files, with citations. This
page gets you there two ways: the browser for novices, the terminal for
everyone else. Pick one; you can switch at any time.

## Contents

- [The browser path](#the-browser-path)
- [The terminal quickstart](#the-terminal-quickstart)
- [Before you start: a database and an embedder](#before-you-start-a-database-and-an-embedder)
- [Run the demo](#run-the-demo)
- [Your own transcripts](#your-own-transcripts)
- [Where to go next](#where-to-go-next)

## The browser path

The browser app is built once from `ui_src/`. Install the package, then
build the app (Node 24 and pnpm are build-time tools only), then run two
commands:

```bash
pip install -e .
pnpm -C ui_src install
pnpm -C ui_src build
corpus-kb setup
corpus-kb start
```

`corpus-kb setup` starts the bundled Postgres container and loads the
schema. `corpus-kb start` starts the server and opens your browser at
`http://localhost:8010/app/`. Set `CORPUS_KB_UI_OPEN=0` to keep the tab
closed.

The **Setup** stage shows the system's health in plain language. More
stages — Ingest, Ask, Review, and the rest — land through the UI SPA
program ([build plan](research/ui-spa-program-plan.md)). Until then, the API and
the CLI cover every operation (see the terminal path below). Details:
[UI.md](UI.md).

## The terminal quickstart

From zero to a working system in about ten minutes. Each step is one
command. Each command says what it does and what success looks like.

1. Clone the repo.

   ```bash
   git clone https://github.com/moliver28/corpus-kb.git
   cd corpus-kb
   ```

2. Install the package.

   ```bash
   pip install -e .
   ```

   Success: `corpus-kb --version` prints a version number.

3. Start the database and load the schema.

   ```bash
   corpus-kb setup
   ```

   Success: the last line says the setup finished. The database answers
   on host port **5433**. No Docker? See [INSTALL.md](INSTALL.md) for
   the native-Postgres path.

4. Check the system.

   ```bash
   corpus-kb doctor
   ```

   Read-only checks. Success: every line says `OK` or `SKIP`. A `FAIL`
   line names its fix.

5. Pull an embedding model that fits the schema.

   ```bash
   ollama pull qwen3-embedding:4b
   ```

   The bundled schema stores `halfvec(2560)` vectors. `qwen3-embedding:4b`
   (2560 dimensions) matches it. Another model width needs a schema
   migration first. The ingest guard and `corpus-kb doctor` catch a
   mismatch and print the fix. The model↔schema matrix is in
   [ADMIN.md](ADMIN.md).

6. Start the server.

   ```bash
   corpus-kb start --transport http --port 8010
   ```

7. Ingest one file from another terminal.

   ```bash
   curl -X POST http://localhost:8010/api/ingest/file \
     -H "Content-Type: application/json" \
     -d '{"file_path": "src/corpus_kb/server_wiring.py"}'
   ```

8. Search it.

   ```bash
   corpus-kb search "how does startup work"
   ```

   Success: ranked scored results. Add `--json` for the machine
   envelope.

Prefer one guided command instead? `corpus-kb quickstart` probes
Ollama, resolves Postgres, applies the schema, and prints the doctor
report. New to qualitative coding? Run `corpus-kb research demo` after
step 5; the demo needs a research-grade embedder.

## Before you start: a database and an embedder

The research pipeline needs two things on your machine before any command
works, and `corpus-kb doctor` verifies both (read-only diagnostics).

**1. A Postgres database.** Two first-class paths - pick whichever fits your
machine:

- **Docker path** (recommended if you have Docker):
  `corpus-kb setup` starts a bundled Postgres stack, creates the database,
  and runs migrations. Follow [INSTALL.md](INSTALL.md) if Docker is absent.
- **Bring-your-own path** (no Docker needed): point Corpus-KB at any
  Postgres 15+ server you already run by setting one environment variable.
  A native Postgres usually listens on 5432 (the bundled compose stack
  publishes 5433 instead — see [OPERATIONS.md](OPERATIONS.md)):

  ```bash
  export CORPUS_KB_DATABASE_URL=postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
  ```

  Every research command and `corpus-kb doctor` honor it - doctor probes
  THAT deployment. The database user needs the grants from
  [INSTALL.md step 2](INSTALL.md) (on Postgres 15+ the `GRANT CREATE ON
  SCHEMA public` line is required).

**2. A research-grade embedder.** Research embeddings require at least
1024 dimensions. The shipped `qwen3-embedding:4b` (2560) qualifies: the
pipeline MRL-slices it to the research width, never padding. Pull it and
point the config at it:

```bash
ollama pull qwen3-embedding:4b
```

`corpus-kb doctor` reports the embedder config status with the exact fix
when something is off. It also counts degraded (zero-vector) chunks and
incomplete ingest runs. The demo fail-fasts with the same guidance if the
configured embedder would abstain.

## Run the demo

The fastest way to see the research pipeline is the narrated demo. It
bundles a two-interview mini corpus and a small codebook under
`docs/demo-corpus/`. It walks through ingest, coding, review, the
governance report, and a cited notebook answer (~5 minutes):

```bash
corpus-kb research demo
```

With the database and embedder from the previous section in place, no other
arguments are needed. Re-runs are safe: file and text hashes make
already-ingested files no-ops.

## Your own transcripts

Real transcripts go in as files. The sections below walk one full pass:
ingest, codebook, coding, review, and a cited answer. The full command
reference is [research.md](research.md).

### Ingest your first transcript

Transcripts go in as files (`.txt` first-class, plus `.vtt`, `.srt`,
`.csv`, `.docx`). A plain-text transcript needs `Speaker: text` lines:

```bash
# one file
corpus-kb research ingest-transcript interviews/dana.txt

# a whole folder, watched for new drops
corpus-kb research ingest interviews/ --watch
```

The general-purpose front door for any file or directory is
`corpus-kb ingest <path>`. Add `--source-type transcript` to force the
transcript strategy. Add `--watch` to tail a folder with auto-heal of
embedding-outage failures. Add `--json` for machine-readable output. Its
exit codes are stable for scripting: `0` everything ingested, `1` partial
success, `2` total failure, `3` configuration error. Repair any chunks
degraded by an embedding outage afterwards with
`corpus-kb ingest-reembed`.

Ingest parses speaker turns and maps roles heuristically (the question-asker
becomes the moderator). It links question-answer exchanges and records
everything as events. Projections build the read models. Re-runs process
only new or changed files.

### Build a codebook

Two ways to get a codebook version:

1. **Inductive first pass** (optional `inductive` extra):
   - `corpus-kb coding inductive` clusters the corpus and proposes codes.
   - Promote with:
     `corpus-kb codebook promote --proposed-id <id> --name "Code name" --definition "..."`.
   - Small or homogeneous corpora may produce zero proposals (every unit
     clusters to noise). The command says so honestly and suggests adding
     transcripts or seeding directly.
2. **Direct seeding** from your own gold exemplars, as the demo does:
   - Codes need gold exemplar passages (quoted verbatim from your transcripts).
   - The deductive run builds prototypes from them.

For reliable automated thresholds you want at least 20 gold exemplars per
code. Below that, coding still runs but routes to review (see the demo).

### Run coding

```bash
corpus-kb coding run --codebook-version-id <uuid> [--project-id <uuid>]
```

The summary reports explicit / question-dependent / review splits. Assignments
land in the review queue whenever thresholds are gray, ambiguous, or
unreliable.

### Review assignments

```bash
corpus-kb review accept <assignment-id> --reviewer "your-name"
corpus-kb review override <assignment-id> --reviewer "your-name" --note "why"
```

### Ask the notebook

```bash
corpus-kb research ask "How do transportation problems shape food access?" \
  --project-id <uuid>
```

Every answer sentence carries an exchange-level citation (document, speaker,
timestamp/turn range). Add `--retrieval-only` for evidence without generation.

## Where to go next

- [UI.md](UI.md) — the four browser pages in detail.
- [OBSERVABILITY.md](OBSERVABILITY.md) — trace every operation with OpenTelemetry.
- [EVALUATION.md](EVALUATION.md) — score retrieval and answers on your own golden set.
- [NOTEBOOKS.md](NOTEBOOKS.md) — marimo notebooks served at `/notebooks`.
- [OPERATIONS.md](OPERATIONS.md) — ports, exit codes, and the operator's contract.
- [understanding-your-report.md](understanding-your-report.md) — every report field in plain language.
