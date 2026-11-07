---
name: corpus-ingest
description: Load files, folders, or web pages into one study's corpus. Use when the user wants to add documents, PDFs, transcripts, meeting notes, .vtt or .srt files, a website, or says "ingest". Also use after the session nudge suggests starting a study.
---

# Corpus Ingest

Load documents into one study's corpus so `corpus-rag ask` and
`corpus-rag search` can find them. A study is one slug with one
database.

## Steps

1. Pick the study. Pass `-p <slug>` when you know it. Otherwise the
   active study receives the files: set it once with
   `corpus-rag use <slug>`, or list studies with `corpus-rag list`. No
   study yet? Create one with `corpus-rag provision <slug>` (see the
   corpus-admin skill).
2. One file:

   ```bash
   corpus-rag ingest "<path to file>" -p <slug>
   ```

3. A folder of files:

   ```bash
   corpus-rag ingest "<path to folder>" -p <slug>
   ```

4. A web page, through the op runner:

   ```bash
   corpus-rag op ingest_url -p <slug> url="<url>"
   ```

   Add `--json '{"crawl": true}'` to follow same-site links.
5. Check the result, then run status before you report:

   ```bash
   corpus-rag status -p <slug>
   ```

   The status line says how many files ingested and failed since the
   last status.

## File notes

- PDF, DOCX, PPTX, HTML, and text files work out of the box.
- Transcripts: `.vtt` and `.srt` files ingest as text. Keep their names
  clear (date and speaker help).
- Google Slides: export the deck to `.pptx` first, then ingest the file.
- Large folders: start with one folder, then check status before adding
  more.
- The plugin starts the study's server when it is down; the first
  command may take a few seconds longer.

## Point a study folder at the study

Inside a study folder, the plugin hooks do the work for you. They
print the study block at session start and queue new and changed files
for ingest. They also nudge toward `corpus-rag search` instead of
reading transcripts by hand. A folder counts as a study folder when it holds a
`.claude/corpus.json` that names a registered study:

```bash
mkdir -p .claude
printf '{"slug": "my-study", "watch": ["interviews"]}' > .claude/corpus.json
corpus-rag provision my-study
```

The keys: `slug`, `watch` (folders to scan), `exclude` (glob list that
replaces the defaults), `auto_ingest` (default true), and `enabled`.
Set `enabled` false to silence every hook in this folder.

Do not call bare `corpus-kb ingest`. It hits the default database, not
the study, and a stale `corpus-kb` can sit first on PATH.
