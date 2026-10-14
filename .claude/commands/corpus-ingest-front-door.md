---
description: "Top-level ingest front door: files/dirs, watch auto-heal, status, reembed"
argument-hint: "<path> [--watch] [--source-type <strategy>] [--reembed]"
---
# corpus-ingest-front-door — ingest files, watch, status, reembed

The top-level ingest front door: file/directory ingest with honest exit
codes, a watched drop folder that auto-heals embedding outages, and the
run-ledger surfaces.

- Ingest one file or a whole directory:

  ```
  corpus-kb ingest <path> [--source-type transcript]
  ```

- Watch a directory; runs that failed ONLY because embeddings were
  unavailable are re-attempted automatically once the provider is back
  (health probe + backoff + capped attempts):

  ```
  corpus-kb ingest <dir> --watch [--no-heal]
  ```

- Exit codes (stable for scripting, printed in --help): 0 ok, 1 partial,
  2 failed, 3 config.

- Status and re-embed surfaces (all flags via --help):

  ```
  corpus-kb ingest-status
  corpus-kb ingest-reembed [--limit N] [--doc-id <uuid>]
  ```
