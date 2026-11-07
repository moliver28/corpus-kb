---
description: Load files, folders, or web pages into one study's corpus. Uses the corpus-ingest skill.
---

Ingest documents into a study's corpus.

Follow the `corpus-ingest` skill. Take the path or URL from the
conversation. Pick the study (a `-p <slug>` the user names, the active
study, or the folder's `.claude/corpus.json`). Run the matching
`corpus-rag ingest` command, then check `corpus-rag status -p <slug>`.
Report what loaded and what failed.

$ARGUMENTS
