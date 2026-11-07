---
name: setup
description: Guided setup for a research folder. Run when the user says yes to the setup offer, or asks to set a corpus study up in this folder. Runs preflight, a PII scan, a few questions, and a journaled apply that can be undone.
disable-model-invocation: true
---

# Setup a Corpus Study in This Folder

Turn this folder of transcripts into a working study: a database, a
seed file the user can search right away, and the rest of the files
queued. Every step is journaled in `.claude/corpus.setup.json` and can
be undone with `corpus-rag setup --off`.

## Steps

1. Run `corpus-rag setup --questions`. Stop on exit 1: read the
   `failed` rows, print each `Fix:` line to the user, and stop.
2. The payload lists `pii_hits`: files with email addresses or
   person-like names (paths, kinds, and counts only — never quote the
   text). List these file names for the user before asking anything.
3. Ask the questions from `calls`, unchanged, in order, with the
   AskUserQuestion tool (one call per group of four).
4. Save the answers keyed by question header. For a UX research study
   add `"uxr": true` so the allow rules include `uxr insights`.
5. Run `corpus-rag setup --apply --answers '<json>'`.
6. On failure: print the `failed_step`, tell the user they can re-run
   `setup --apply` to resume, or run `corpus-rag setup --off` to undo.
   Do not start over.
7. On success: run each `Verify:` command in order and report the
   results. The seed file is searchable; the rest of the files stay in
   the queue and drain on their own.

## Notes

- `setup --questions` changes nothing on disk. It is safe to re-run.
- `queue autorun` is global for all studies on this machine; the
  question says so. Ask it unchanged.
- A "No" on the cloud-model question keeps the study local only
  (`local_only` in the gate state); no transcript text reaches a cloud
  model.
- `corpus-rag setup --off` undoes the allow rules and the autorun
  change, stops the server, and silences the folder. The database and
  the journal stay.
