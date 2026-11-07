---
description: "Event-store health: topics, notification id, checkpoints, dead letters, runs"
---
# corpus-events — event-store health

One read over the event store, the projection checkpoints, the dead
letters, and the recent ingest runs.

```
corpus-kb events [--json]
```

- Event counts by topic and the latest notification id come from the
  event store: the Postgres table, or the SQLite sidecar on the duckdb
  backend. The engine resolves the store; do not open store files.
- Checkpoints, dead letters, and the last five ingest runs are scoped
  to one tenant.
- All flags: `corpus-kb events --help`
