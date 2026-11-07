---
name: corpus-admin
description: Set up and maintain Corpus-KB studies. Use when the user wants to create a study (provision), list or switch studies, apply migrations, re-embed chunks, back a study up, check event-store health, or run engine diagnostics for one study.
---

# Corpus Admin

Set up and maintain studies. A study is one slug with one database, one
port, and one server. These commands are safe to run any time; none of
them deletes data without saying so first.

## Steps

1. Pick the study. Pass `-p <slug>` when the command takes it. Commands
   without `-p` use the active study (`corpus-rag use <slug>`), or the
   slug in the folder's `.claude/corpus.json` when one exists.

2. Create a study:

   ```bash
   corpus-rag provision <slug> --backend postgres
   ```

   The engine creates (or adopts) the database, applies the schema, and
   the plugin registers it with a port. For a local single-user study
   use `--backend duckdb`. On failure the output prints the exact
   cleanup command.

3. Everyday lifecycle:

   ```bash
   corpus-rag list                 # studies, ports, up or down, active mark
   corpus-rag use <slug>           # set the active study
   corpus-rag serve -p <slug>      # start this study's server now
   corpus-rag stop -p <slug>       # stop it
   ```

4. Maintenance:

   ```bash
   corpus-rag migrate -p <slug>              # pending migrations; --all for every study
   corpus-rag reembed -p <slug> [--limit N]  # rebuild missing vectors
   corpus-rag backup <slug>                  # pg_dump, or DuckDB file copy
   corpus-rag events -p <slug>               # event-store health
   corpus-rag doctor -p <slug>               # engine diagnostics for this study
   ```

   On DuckDB studies the plugin stops the study's server first (one
   writer per file) and says so.

5. Check the result with `corpus-rag status -p <slug>`. It reports the
   doctor verdict, the counts, the queue, and what changed since the
   last status.

## The op inventory

Do not hard-code op names or counts; ask the engine at run time:

```bash
corpus-rag ops                  # every op, one line each, live count
corpus-rag ops --family search  # one family
corpus-rag op <name> -p <slug> key=value ...
```

`op` validates the name and arguments against the live inventory. It
sends one HTTP call to the study's server and prints the result envelope.
Long ingest ops get a 7200 second timeout.

## Evidence rule

Only participant sessions are evidence. Cite them as `[S:<source-slug>]`
in anything you write from a study. Never present a summary or an
inference as a participant quote.

## Notes

- `provision --project-id <id>` keeps the 0.7.x raw-table behavior; the
  standalone name for it is `raw-provision`.
- Idle study servers stop after 30 minutes of disuse; the current study
  never stops mid-command.
- The plugin never prints a password: DSNs come out masked.
