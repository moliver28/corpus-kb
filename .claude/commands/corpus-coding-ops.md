---
description: "Chunk-coding ops: codebook load, pool, batch, reliability, floors, saturation, status, export"
argument-hint: "<file> | --codebook-version-id <uuid> | --cells <file>"
---
# corpus-coding-ops — the chunk-coding operations

Deductive coding over stored chunks: load a codebook, pool candidates,
code batches, measure reliability, calibrate floors, watch saturation.

- Load and embed a codebook JSON file (answers its codebook version id):

  ```
  corpus-kb coding embed-codebook <codebook.json>
  ```

- Run pooling and coverage reconciliation for one codebook version:

  ```
  corpus-kb coding pool --codebook-version-id <uuid>
  ```

- Code a batch of cells. The cells file holds a JSON list of
  {"chunk_id": ..., "code_ids": [...]} entries:

  ```
  corpus-kb coding code-batch --cells <cells.json> [--batch-id <id>]
  ```

- Reliability, floors, saturation, and the read ops (all flags via
  --help):

  ```
  corpus-kb coding reliability
  corpus-kb coding calibrate-floors <labels.json>
  corpus-kb coding saturation [--batch-id <id>]
  corpus-kb coding status
  corpus-kb coding export [--format csv]
  ```

- embed-codebook, pool, and code-batch are long ops: they can run for
  minutes on a real corpus.
