# API Reference

Corpus-KB exposes three protocols: MCP over stdio, HTTP REST, and JSON-RPC over a Unix socket or Windows named pipe. This page lists the HTTP routes and gives curl examples for each.

---

## Protocols

| Protocol | Use case | Address |
|----------|----------|---------|
| MCP stdio | Editor agents | `corpus-kb --transport stdio` |
| HTTP | Scripts, browsers, integrations | `http://localhost:8010` |
| JSON-RPC socket | Local inter-process calls | `/tmp/corpus-kb.sock` or `127.0.0.1:8011` on Windows |

There is no authentication today. Corpus-KB is intended to run locally.

All responses use `application/json`. Errors look like:

```json
{"status": "error", "error": "...", "error_type": "..."}
```

---

## Ingest

### POST /api/ingest/file

Ingest a single file from disk.

```bash
curl -X POST http://localhost:8010/api/ingest/file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "src/corpus_kb/server_wiring.py"}'
```

Request body:

```json
{
  "file_path": "src/corpus_kb/server_wiring.py",
  "content": "optional override",
  "source_type": "optional code/markdown/text",
  "tenant_id": "00000000-0000-0000-0000-000000000001"
}
```

### POST /api/ingest/text

Ingest raw text.

```bash
curl -X POST http://localhost:8010/api/ingest/text \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "source": "notes", "source_type": "text"}'
```

### POST /api/ingest/directory

Recursively ingest a directory.

```bash
curl -X POST http://localhost:8010/api/ingest/directory \
  -H "Content-Type: application/json" \
  -d '{"directory_path": "src", "recursive": true}'
```

### GET /api/documents

List documents with pagination.

```bash
curl "http://localhost:8010/api/documents?limit=10&offset=0"
```

### DELETE /api/documents/{doc_id}

Delete a document.

```bash
curl -X DELETE http://localhost:8010/api/documents/{doc_id}
```

---

## Search

### POST /api/search

Hybrid vector + FTS search.

```bash
curl -X POST http://localhost:8010/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "how does startup work", "k": 10, "source_type": "code"}'
```

### POST /api/search/similar

Find chunks similar to a given chunk.

```bash
curl -X POST http://localhost:8010/api/search/similar \
  -H "Content-Type: application/json" \
  -d '{"chunk_id": "...", "k": 5}'
```

### POST /api/search/context

Search with surrounding chunks.

```bash
curl -X POST http://localhost:8010/api/search/context \
  -H "Content-Type: application/json" \
  -d '{"query": "authentication", "k": 5, "context_chunks": 2}'
```

---

## SQL queries

### POST /api/query/sql

Run a read-only SQL query.

```bash
curl -X POST http://localhost:8010/api/query/sql \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT source_type, COUNT(*) FROM documents GROUP BY source_type"}'
```

Parameterized queries:

```bash
curl -X POST http://localhost:8010/api/query/sql \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM chunks WHERE source_type = $1 LIMIT 10", "params": {"1": "code"}}'
```

---

## Graph

### POST /api/entities

Add an entity.

```bash
curl -X POST http://localhost:8010/api/entities \
  -H "Content-Type: application/json" \
  -d '{"name": "Authentication", "entity_type": "concept"}'
```

### GET /api/entities

List entities, optionally filtered by type.

```bash
curl "http://localhost:8010/api/entities?entity_type=concept&limit=50"
```

### POST /api/relations

Add a relation.

```bash
curl -X POST http://localhost:8010/api/relations \
  -H "Content-Type: application/json" \
  -d '{
    "source_entity_id": "...",
    "target_entity_id": "...",
    "relation_type": "RELATED_TO",
    "weight": 1.0
  }'
```

### POST /api/graph/search

Search entities by name.

```bash
curl -X POST http://localhost:8010/api/graph/search \
  -H "Content-Type: application/json" \
  -d '{"query": "auth", "entity_type": "concept", "limit": 20}'
```

### POST /api/graph/bfs

BFS traversal from an entity.

```bash
curl -X POST http://localhost:8010/api/graph/bfs \
  -H "Content-Type: application/json" \
  -d '{"start_entity_id": "...", "max_depth": 3}'
```

### GET /api/graph/relations/{entity_id}

Get all relations for an entity.

```bash
curl http://localhost:8010/api/graph/relations/{entity_id}
```

---

## Tags and metadata

### POST /api/tags

Create a tag.

```bash
curl -X POST http://localhost:8010/api/tags \
  -H "Content-Type: application/json" \
  -d '{"name": "reviewed", "color": "green", "description": "Already reviewed"}'
```

### POST /api/documents/{doc_id}/tags

Apply a tag to a document.

```bash
curl -X POST http://localhost:8010/api/documents/{doc_id}/tags \
  -H "Content-Type: application/json" \
  -d '{"tag": "reviewed"}'
```

### GET /api/documents/{doc_id}/tags

List tags on a document.

```bash
curl http://localhost:8010/api/documents/{doc_id}/tags
```

### POST /api/metadata

Set metadata.

```bash
curl -X POST http://localhost:8010/api/metadata \
  -H "Content-Type: application/json" \
  -d '{"key": "priority", "value": "high", "doc_id": "..."}'
```

### GET /api/metadata

Get metadata.

```bash
curl "http://localhost:8010/api/metadata?key=priority&doc_id=..."
```

---

## Admin

### GET /api/versions

List event store versions.

```bash
curl http://localhost:8010/api/versions
```

### GET /api/stats

Database statistics.

```bash
curl http://localhost:8010/api/stats
```

### GET /api/tables

List tables and schemas.

```bash
curl http://localhost:8010/api/tables
```

### GET /api/document-stats

Aggregate document statistics.

```bash
curl http://localhost:8010/api/document-stats
```

---

## Research subsystem

The research/coding surfaces (ingest, coding runs, promotion, review,
notebook views) are reachable over HTTP. Each route delegates to the SAME
shared tool layer as the CLI and MCP (one handler per operation). Body
fields mirror the CLI flags; `tenant_id` defaults to the shared
placeholder.

| Route | Method | Delegates to | Body (required in bold) |
|-------|--------|--------------|--------------------------|
| `/api/research/ingest-transcript` | POST | `research_ingest_transcript` | **file_path**, project_id, title, force, tenant_id |
| `/api/research/ingest` | POST | `research_ingest` | **path**, project_id, force, tenant_id |
| `/api/coding/run` | POST | `coding_run` | **codebook_version_id**, project_id, cal_alpha, tenant_id |
| `/api/coding/inductive` | POST | `inductive_run` | project_id, tenant_id |
| `/api/codebook/promote` | POST | `codebook_promote` | **proposed_id, name, definition**, inclusion, exclusion, label, tau_dup, tenant_id |
| `/api/research/report` | POST | `research_report` | codebook_version_id, level, project_id, tenant_id |
| `/api/review/execute` | POST | `review_execute` | **assignment_id, decision, reviewer**, note, tenant_id |
| `/api/notebook/ask` | POST | `notebook_ask` | **question, project_id**, k, retrieval_only, filters..., tenant_id |
| `/api/notebook/evidence` | POST | `notebook_evidence` | **code**, project_id, version_id, limit, tenant_id |
| `/api/notebook/uncoded` | POST | `notebook_uncoded` | project_id, version_id, k, tenant_id |
| `/api/notebook/overlap` | POST | `notebook_overlap` | project_id, version_id, tenant_id |

`research-cycle` (interactive postures, approval boundary, watch mode) and
`research-demo` (CLI-only by design) intentionally have NO HTTP route —
the documented parity allowlist in
`src/corpus_kb/surface_registry_parity.py`.

```bash
curl -X POST http://localhost:8010/api/research/report   -H "Content-Type: application/json" -d "{}"
```

---

## Release lifecycle (U5, v6 §6)

The release lifecycle ships on all three transports with identical
payloads: CLI `corpus-kb release ...`, MCP `release_*` tools, and these
routes. One handler per operation (`handlers/release_handlers.py`).

| Route | Method | Body (required in bold) |
|-------|--------|--------------------------|
| `/api/release/assess` | POST | **codebook_version_id**, profile, run_id, tenant_id |
| `/api/release/request` | POST | **codebook_version_id, creator**, codebook_id, profile, release_id, tenant_id |
| `/api/release/review` | POST | **codebook_version_id, release_id**, tenant_id |
| `/api/release/release` | POST | **codebook_version_id, release_id, approver**, waivers ([[gate_id, justification]...]), run_id, tenant_id |
| `/api/release/status` | GET | query: **codebook_version_id**, tenant_id |
| `/api/release/compare` | POST | **codebook_version_id, old_release_id, new_release_id**, tenant_id |

Honest status codes: `assess` reports `not_evaluable` gates as 200 data
(it writes nothing); a refused release (enforcement=enforce, unmet
required gates) is HTTP 409 with the structured `blocked` payload;
unknown ids and malformed input are 400 with
`{"status": "error", ...}`.

```bash
# compose every gate over current evidence (writes nothing)
curl -X POST http://localhost:8010/api/release/assess   -H "Content-Type: application/json"   -d '{"codebook_version_id": "<uuid>"}'

# the D1 status view (stage, blockers, sufficiency, next command)
curl "http://localhost:8010/api/release/status?codebook_version_id=<uuid>"
```

---

## MCP tool reference

The same operations are available through MCP. See [FEATURES.md](FEATURES.md#mcp-tool-reference) for the full table.

---

## Next steps

- [Install guide](INSTALL.md)
- [Features overview](FEATURES.md)
- [Admin guide](ADMIN.md)
- [Development guide](DEVELOPMENT.md)
- [FAQ](FAQ.md)
