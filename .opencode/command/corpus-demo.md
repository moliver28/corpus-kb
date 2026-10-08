---
description: Run the full pipeline on the bundled demo corpus
---
# corpus-demo — narrated demo pipeline

Run the full qualitative-coding pipeline on the bundled two-interview demo
corpus (docs/demo-corpus/): ingest, codebook seed, coding run, review gate,
governance report, and a cited notebook answer.

```
corpus-kb research demo
```

- Requires `corpus-kb setup` only (Postgres + migrations + models).
- Every stage narrates what it is doing and links the relevant docs.
- Re-runs are safe: file and text hashes make ingested files no-ops.
- CLI-only by design; all flags: `corpus-kb research demo --help`
