---
name: corpus-ingest
description: Load files, folders, or web pages into the Corpus-KB corpus. Use when the user wants to add documents, PDFs, transcripts, meeting notes, .vtt or .srt files, a website, or says "ingest". Also use after the session nudge suggests starting a study.
---

# Corpus Ingest

Load documents into the corpus so `corpus-kb ask` and `corpus-kb search` can
find them.

## Steps

1. One file:

   ```bash
   corpus-kb ingest "<path to file>"
   ```

2. A folder of files:

   ```bash
   corpus-kb ingest "<path to folder>"
   ```

3. A web page:

   ```bash
   corpus-kb ingest-url "<url>"
   ```

   Add `--crawl` to follow same-site links.

4. Check the result:

   ```bash
   corpus-kb ingest-status
   ```

## File notes

- PDF, DOCX, PPTX, HTML, and text files work out of the box.
- Transcripts: `.vtt` and `.srt` files ingest as text. Keep their names clear
  (date and speaker help).
- Google Slides: export the deck to `.pptx` first, then ingest the file.
- Large folders: start with one folder, then check `ingest-status` before
  adding more.

## Register the study

The session hint stops once a study is registered. After your first
successful ingest, create the marker:

```bash
mkdir -p ~/.corpus-kb/plugin
touch ~/.corpus-kb/plugin/study-registered
```

On Windows use `%USERPROFILE%\.corpus-kb\plugin` in place of `~/.corpus-kb/plugin`.
