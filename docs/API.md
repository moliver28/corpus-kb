# API Reference

Corpus-KB exposes MCP over stdio, HTTP REST, and JSON-RPC over a Unix socket or Windows named pipe. This page lists the HTTP routes and gives curl examples for each; the MCP tool reference lives in [FEATURES.md](FEATURES.md).

---

## Protocols

| Protocol | Use case | Address |
|----------|----------|---------|
| HTTP | Scripts, the browser UI, integrations | `http://localhost:8010` |
| OpenAI-compatible (v1) | OpenAI SDK clients and chat frontends | `http://localhost:8010/v1` |
| JSON-RPC socket | Local inter-process calls | `/tmp/corpus-kb.sock` or `127.0.0.1:8011` on Windows |
| MCP stdio | Editor agents | `corpus-kb --transport stdio` (every catalogue operation is a tool) |

The `mcp-configs/` files (Claude Code, Cursor, Codex) and the root
`opencode.json` (OpenCode) launch the stdio server. Generic MCP clients
(Hermes, Cline, and others) need no config file; they point at the same
command. See [harness-distribution.md](research/harness-distribution.md).

There is no authentication today. Corpus-KB is intended to run locally.

All responses use `application/json`. Errors look like:

```json
{"status": "error", "error": "...", "error_type": "..."}
```

The `/v1` adapter below uses the OpenAI error shape instead.

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
  "source_type": "optional strategy override or hint, e.g. code/text/transcript",
  "tenant_id": "00000000-0000-0000-0000-000000000001"
}
```

`source_type` is validated against the ingestion-strategy registry. An
explicit value overrides content detection; an unknown value is rejected
with the standard error envelope. The file is never decoded as text on
the surface. The ingestion strategy partitions the file, so binary
formats (PDF, DOCX, PPTX) work.

### POST /api/ingest/text

Ingest raw text.

```bash
curl -X POST http://localhost:8010/api/ingest/text \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "source": "notes", "source_type": "text"}'
```

`source_type` is the document type. It must be one of `code`, `markdown`,
`text`, `transcript`; anything else returns
`{"status": "error", "message": "Invalid source_type: <value>"}` — the
same envelope the tool layer returns.

Ingest envelopes (`file`, `text`, `directory`) match the shared tool
envelope exactly. They add the declared additive event-store wrapper
keys `aggregate_id` and `aggregate_version`. The key
`event_sourcing_error` appears only when event sourcing failed after a
successful pipeline. The socket
methods `ingest_file` and `ingest_text` return the same shape.

### POST /api/ingest/directory

Ingest one directory in one shared walk.

```bash
curl -X POST http://localhost:8010/api/ingest/directory \
  -H "Content-Type: application/json" \
  -d '{"directory_path": "src", "recursive": true}'
```

The walk is the tool layer's `ingest_directory`; CLI, MCP, and HTTP all
share it. One filter applies on every surface: a file is ingested only
when its suffix is in `ingest_watch.WATCH_SUFFIXES` (`.txt`, `.md`,
`.rst`, `.pptx`, and common code suffixes). `recursive` defaults to
`true`; `false` walks the top level only.

The response is the union envelope `{status, directory, files_processed,
total_documents, total_entities, results}`:

- `status` is `success` when the walk completed. Per-file failures stay
  inside `results`. It is `error` for a bad path or an unknown
  `source_type`; then nothing was ingested and the body is
  `{status, message}` with HTTP 200.
- `directory` echoes the requested path.
- `files_processed` counts the files attempted (`len(results)`).
- `total_documents` counts the files whose own ingest returned
  `success`.
- `total_entities` sums `entity_count` over those successes.
- `results` holds the per-file envelopes in walk order. Each success
  carries the additive `aggregate_id`/`aggregate_version` audit keys.

### GET /api/ingest/status

Recent runs from the `ingest_runs` ledger: status (`running` /
`completed` / `failed`), start/finish hashes, and per-stage results for
each ingest.

```bash
curl http://localhost:8010/api/ingest/status
```

The response is `{status, ledger_present, runs, incomplete_runs, degraded}`
— recent `ingest_runs` rows (JSON-safe), the count of runs never closed,
and degraded-vector counts. When migration 022 has not run yet the
response is `{"status": "unavailable", "ledger_present": false, ...}`
with HTTP 503 rather than a hard error payload.

### POST /api/ingest/reembed

Re-embed chunks flagged degraded (zero-vector fallback) once the embedding
provider is back. Accepts optional `limit` and `doc_id` selectors;
repaired rows flip `degraded` back to `false`.

```bash
curl -X POST http://localhost:8010/api/ingest/reembed \
  -H "Content-Type: application/json" \
  -d '{"limit": 100}'
```

The response reports honest counts: `{"status": "...", "scanned": N,
"fixed": N, "failed": N}`. The fields `degraded`/`warning` appear when
the primary embedding provider needed attention. The response is
`{"status": "unavailable"}` when migration 022's degraded flags are not
present yet.

### GET /api/documents

List documents with pagination.

```bash
curl "http://localhost:8010/api/documents?limit=10&offset=0"
```

### DELETE /api/documents/{doc_id}

Delete one document. MCP (`delete_document`) and this route share ONE
implementation: the tool's row delete.

```bash
curl -X DELETE http://localhost:8010/api/documents/{doc_id}
```

The delete runs in one transaction with the caller's tenant, so RLS
applies. The route deletes inside the default tenant unless `tenant_id`
is sent. Chunks, vectors, tags, metadata, and research rows cascade. Entity and
relation rows stay; their document links become NULL. Event-store rows
persist.

The delete is idempotent: an unknown ID still answers
`{"status": "success", "document_id": "..."}` with HTTP 200. On the HTTP
surface the command handler then adds the audit keys
`aggregate_id`/`aggregate_version`. The key `event_sourcing_error`
appears when the audit write failed after the successful delete.

Neither `ingest_directory` nor `delete_document` has a socket method; the
parity catalogue records that by listing no socket surface.

---

## Search

### POST /api/search

Hybrid vector + FTS search.

```bash
curl -X POST http://localhost:8010/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "how does startup work", "k": 10, "source_type": "code"}'
```

The response envelope is degraded-aware. The field `degraded_excluded`
reports `{count, reasons}`. `count` is the number of candidate rows the
degraded filter excluded (0 when the filter removed nothing). `reasons`
lists the recorded arm impairments (zero-query embedding, vector-arm
error, no embedder, filter-active). A legitimately empty search (no
candidates existed at all) returns plain `success` with `count: 0` and no
remedy. The status is `degraded_empty` when results came back empty AND at
least one impairment was recorded. The remedy: start Ollama, then
run `corpus-kb ingest-reembed`. With tracing on, the envelope also carries
`trace_id`, the 32-char hex id of the active OpenTelemetry trace. It is
absent when tracing is off (see docs/OBSERVABILITY.md).

### Result ordering

Search results have a deterministic order: score descending, then chunk id
ascending. Tied scores never permute between runs. A harness can snapshot
results and compare them across runs. This holds for `/api/search`,
`/api/search/similar`, and `/api/search/context`. The research search
endpoints order fused ties by unit or exchange id the same way. Migration
024 sets this tie order inside `corpus.rrf_fusion`.

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

### POST /api/ask

Answer a question from retrieved evidence.

```bash
curl -X POST http://localhost:8010/api/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "how does startup work", "k": 5}'
```

The route searches with the hybrid retriever, then generates with the
local LLM at temperature 0. Every answer sentence must carry an evidence
index like `[1]`. Sentences without a valid index are dropped, never
shown uncited.

Response body (`status`, `result`, `degraded_excluded`):

```json
{
  "status": "success",
  "result": {
    "answer": "The startup order is documented [1].",
    "sentences": [{"text": "The startup order is documented [1].", "citations": [1]}],
    "sources": [
      {"n": 1, "chunk_id": "...", "source": "runbook.md", "text": "...", "score": 0.9}
    ],
    "verify": {"groundedness": 1.0, "verdicts": [], "abstained": true},
    "model": "qwen3:4b",
    "note": null
  },
  "degraded_excluded": {"count": 0, "reasons": []}
}
```

Honest statuses: `success`, `empty`, and `degraded`. `empty` means no
evidence matched; no LLM call runs. `degraded` means generation failed;
the evidence list ships with a `note`. `degraded_excluded` mirrors
`/api/search` and reports what the degraded filter could not see.

When `judge.enabled` is true, the answer is decomposed into claims and
checked against the sources. `verify.groundedness` is the entailed share.
The verifier abstains rather than guessing: no judge, no claims, or a
judge outage all answer `abstained: true`.

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

Engine selection and the engine swap have no HTTP route. Set
`database.backend` in `config.yaml`, or run `corpus-kb migrate-data`
while the stack is down. See [STORAGE-BACKENDS.md](STORAGE-BACKENDS.md).

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
| `/api/research/ingest-transcript` | POST | `research_ingest_transcript` | **`file_path`**, `project_id`, title, force, `source_type`, `tenant_id` |
| `/api/research/ingest` | POST | `research_ingest` | **path**, `project_id`, force, `source_type`, `tenant_id` |
| `/api/coding/run` | POST | `coding_run` | **`codebook_version_id`**, `project_id`, `cal_alpha`, `tenant_id` |
| `/api/coding/inductive` | POST | `inductive_run` | `project_id`, `tenant_id` |
| `/api/codebook/promote` | POST | `codebook_promote` | **`proposed_id`, name, definition**, inclusion, exclusion, label, `tau_dup`, `tenant_id` |
| `/api/research/report` | POST | `research_report` | `codebook_version_id`, level, `project_id`, `tenant_id` |
| `/api/review/execute` | POST | `review_execute` | **`assignment_id`, decision, reviewer**, note, `tenant_id` |
| `/api/notebook/ask` | POST | `notebook_ask` | **question, `project_id`**, k, `retrieval_only`, filters..., `tenant_id` |
| `/api/notebook/evidence` | POST | `notebook_evidence` | **code**, `project_id`, `version_id`, limit, `tenant_id` |
| `/api/notebook/uncoded` | POST | `notebook_uncoded` | `project_id`, `version_id`, k, `tenant_id` |
| `/api/notebook/overlap` | POST | `notebook_overlap` | `project_id`, `version_id`, `tenant_id` |
| `/api/research/cycle` | POST | `research_cycle` | mode, `project_id`, `ingest_dir`, question, `codebook_version_id`, guide, approve, `halt_on`, `tenant_id` |
| `/api/research/demo` | POST | `research_demo` | (no body) |

The cycle and the demo are long-running and synchronous. One POST runs the
whole pipeline and returns the receipt when it ends. This is the same call
the CLI makes. The cycle's `codebook_promotion` gate stays hard-floored.
No surface automates promotion.

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
| `/api/release/assess` | POST | **`codebook_version_id`**, profile, `run_id`, `tenant_id` |
| `/api/release/request` | POST | **`codebook_version_id`, creator**, `codebook_id`, profile, `release_id`, `tenant_id` |
| `/api/release/review` | POST | **`codebook_version_id`, `release_id`**, `tenant_id` |
| `/api/release/release` | POST | **`codebook_version_id`, `release_id`, approver**, waivers ([[`gate_id`, justification]...]), `run_id`, `tenant_id` |
| `/api/release/status` | GET | query: **`codebook_version_id`**, `tenant_id` |
| `/api/release/compare` | POST | **`codebook_version_id`, `old_release_id`, `new_release_id`**, `tenant_id` |

Honest status codes: `assess` reports `not_evaluable` gates as 200 data
(it writes nothing). A refused release (enforcement=enforce, unmet
required gates) is HTTP 409 with the structured `blocked` payload.
Unknown ids and malformed input are 400 with
`{"status": "error", ...}`.

```bash
# compose every gate over current evidence (writes nothing)
curl -X POST http://localhost:8010/api/release/assess   -H "Content-Type: application/json"   -d '{"codebook_version_id": "<uuid>"}'

# the D1 status view (stage, blockers, sufficiency, next command)
curl "http://localhost:8010/api/release/status?codebook_version_id=<uuid>"
```

---

## OpenAI-compatible API (v1)

Point any OpenAI SDK client at `http://localhost:8010/v1`.
Tested frontends include Open WebUI, LibreChat, and Jan.
In Open WebUI, add an OpenAI connection with that base URL.

The adapter serves three routes:

| Route | Purpose |
|-------|---------|
| `POST /v1/chat/completions` | Grounded answers, JSON or SSE streaming |
| `POST /v1/embeddings` | Embeddings from the configured embedder |
| `GET /v1/models` | The embedding and answer model names |

The flag `server.openai_compat` (default true) gates all three routes.

### POST /v1/chat/completions

The chat route runs the same ask pipeline as `POST /api/ask`.
Retrieval, generation, citations, and verification behave identically.
The last user message becomes the query.
The reply joins the answer and a `Sources:` list into one `content` string.
An `empty` result states that no evidence matched.
A `degraded` pipeline (LLM down) answers HTTP 503 with the note.

```bash
curl -X POST http://localhost:8010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "how does startup work"}]}'
```

The body follows the OpenAI chat schema:

```json
{
  "model": null,
  "messages": [{"role": "user", "content": "how does startup work"}],
  "stream": false,
  "temperature": 0
}
```

`model` is optional and defaults to the configured `llm.model`.
`temperature` is accepted but ignored; generation always runs at 0.
Responses omit `usage` because token counts are not tracked.

Non-streaming replies use the OpenAI completion shape:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1760000000,
  "model": "qwen3:4b",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "The answer [1].\n\nSources:\n1. runbook.md"},
      "finish_reason": "stop"
    }
  ]
}
```

With `"stream": true`, the route emits server-sent events.
The answer is generated in full before the first frame.
Frames then carry the whole answer in small pieces.
The stream ends with a `stop` frame, then `data: [DONE]`.
There is no token-level streaming yet.

### POST /v1/embeddings

This route embeds one string or a list of strings.
The default Ollama provider needs no database for this route.

```bash
curl -X POST http://localhost:8010/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input": ["alpha", "beta"]}'
```

The response carries one indexed `embedding` entry per input.
`model` is optional and defaults to the configured `embedding.model`.
Responses omit `usage` for the same reason as above.

### GET /v1/models

Lists two entries: the embedding model and the answer model.

```bash
curl http://localhost:8010/v1/models
```

```json
{
  "object": "list",
  "data": [
    {"id": "qwen3-embedding:4b", "object": "model"},
    {"id": "qwen3:4b", "object": "model"}
  ]
}
```

### Error shape

Errors use the OpenAI error object, not the `/api` envelope:

```json
{"error": {"message": "...", "type": "invalid_request_error", "code": null}}
```

Malformed bodies answer 400 or 422 with `invalid_request_error`.
A degraded pipeline answers 503 with `service_unavailable`.

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
