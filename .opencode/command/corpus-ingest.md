---
description: Ingest research transcripts (single file or watched drop directory)
---
# corpus-ingest — ingest research transcripts

Load interview / focus-group transcripts into the research knowledge base.

- Single file (auto-detected txt / vtt / srt / csv / docx):

  ```
  corpus-kb research ingest-transcript <path> --project-id <uuid>
  ```

- File, directory, or glob with file-hash + text-hash dedup; add `--watch`
  to tail a drop directory:

  ```
  corpus-kb research ingest <path> [--watch] [--force]
  ```

- All flags: `corpus-kb research ingest --help`
- Re-runs only process new or changed files; do not delete the ingest ledger.
