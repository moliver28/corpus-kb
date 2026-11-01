# Operations reference

This page is the operator's contract. Every number here comes from the code.
When this page and the code disagree, the code wins — and please open an issue.

## Contents

- [Exit codes](#exit-codes)
- [Ports](#ports)
- [Vocabularies](#vocabularies)
- [Tenancy](#tenancy)
- [Embedding model vs schema](#embedding-model-vs-schema)
- [The MCP surface](#the-mcp-surface)
- [Where to go next](#where-to-go-next)

The machine-readable source of truth is the operation inventory:

```bash
corpus-kb operations --indent 0     # every operation: surfaces, params, envelope keys
corpus-kb operations --openapi      # the HTTP surface as an OpenAPI 3.1 document
```

Both run offline. They need no database. Every command below also states its
exit codes in `corpus-kb <command> --help`.

---

## Exit codes

### The ingest family

`corpus-kb ingest`, `ingest-status`, `ingest-reembed`, and `ingest-url`
(`src/corpus_kb/cli/cli_ingest.py`):

| Code | Meaning |
|------|---------|
| 0 | Everything succeeded. `Ctrl-C` during `--watch` also exits 0. |
| 1 | Partial success. Some files, chunks, or pages failed. |
| 2 | Total failure. Every file or page failed, or the fetch was blocked. |
| 3 | Configuration error. Bad path, unknown `--source-type`, database unreachable, or missing migration. |

Per-command nuances:

- `corpus-kb ingest --watch` ends with 0 on a clean summary and 1 when
  watched files failed. It never exits 2; the loop keeps running.
- `corpus-kb ingest --reembed` and `corpus-kb ingest-reembed` exit 3 when
  the ledger is unavailable, 1 when some chunks failed, else 0.
- `corpus-kb ingest-status` exits 0 on a report and 3 when the ledger is
  absent or the database is down. It never exits 1 or 2.

### The topics family

`corpus-kb topics-build` and `corpus-kb topics-list`
(`src/corpus_kb/cli/cli_topics.py`):

| Code | Meaning |
|------|---------|
| 0 | The command answered. This includes the honest no-op outcomes: the feature is disabled, or the corpus is below the minimum topic size. |
| 1 | The build failed. |
| 3 | Configuration state: migration 026 missing, the `[topics]` extra missing, or the database is unreachable. |

Per-command nuances:

- The feature ships dormant (`topics.enabled: false`). `topics-build`
  answers with a no-op envelope naming the flag. That is exit 0, never
  an error.
- A missing `bertopic` package exits 3 with the remedy:
  `pip install 'corpus-kb[topics]'`. The HTTP surface answers 503 with
  `error_type: dependency_missing`; the socket surface answers -32005.
- `topics-list` is never gated by the flag. Reading an existing build
  while the feature is off is safe.
- LLM label reduction never fails the build. A down Ollama keeps the
  c-TF-IDF labels and records the note in the result.

### The search, query, and graph families

`corpus-kb search`, `ask`, `query`, `verify-answer`, `add-entity`,
`add-relation`, `list-documents`, `delete-document`, and `ingest-text` (the
`cli/cli_search.py` / `cli/cli_ask.py` / `cli/cli_query.py` /
`cli/cli_graph.py` / `cli/cli_ingest_text.py` front doors):

| Code | Meaning |
|------|---------|
| 0 | The command answered. An empty `list-documents` or `search` result is still 0. A `verify-answer` abstention is still 0 and is reported. A `delete-document` of an unknown ID is still 0; the delete is idempotent. |
| 1 | The command failed. An error envelope or a `CorpusError` prints and exits 1. |
| 3 | The database is unreachable. Bad DSN, wrong credentials, or the server is down. |

Per-command nuances:

- Every command takes `--json`. It prints the exact handler envelope, so
  the CLI, MCP, HTTP, and socket surfaces return byte-shape-identical
  payloads for one operation.
- `ingest-text -` reads the raw text from stdin.
- `query` prints the routed backend and the ranked hits in human mode.
- `ask` prints the cited answer sentences, the sources, and the
  groundedness when the judge ran. An `ask` outcome of `empty` (no
  evidence) or `degraded` (generation down, evidence only) is still 0;
  the note prints.
- `add-entity` and `add-relation` write through the same aggregate
  command handler the HTTP routes use, so the audit trail is identical.

### The research cycle gates

`corpus-kb research cycle` halts at human gates. Each gate has its own exit
code (`src/corpus_kb/research/cycle_gates.py`):

| Code | Gate |
|------|------|
| 10 | `codebook_promotion` — hard floor. Proposals always await a human. |
| 11 | `interpretive_code_review` |
| 12 | `gray_zone_escalation` |
| 13 | `drift_alarm` |
| 14 | `overlap_conflict` |
| 15 | `threshold_unreliable` |
| 16 | `human_parity_breach` |
| 17 | `unreleased_draft` |
| 20 | Approval denied (`--mode on`). A closed stdin also counts as "no". |
| 21 | Awaiting approval — the caller cannot prompt at all. The MCP tool returns an `awaiting_approval` receipt. |
| 2  | The cycle cannot run. Example: no codebook version exists yet. |
| 0  | The cycle finished. |

To resume: act on the gate, then re-run the same command. The run continues
after its last checkpoint. Config may add gates via `research.cycle.halt_on`.
Config may never remove `codebook_promotion`.

The cycle is long-running and synchronous on every surface. An HTTP POST
to `/api/research/cycle`, the `research_cycle` MCP tool, and the socket
method run the same pipeline. Each call returns the receipt when the run
ends: `status`, `exit_code`, `gate`, `checkpoint_kind`, `resume_hint`.
Promotion stays a human act on every surface. The narrated demo works the
same way. The `/api/research/demo` route, the `research_demo` tool, and
the socket method return a receipt with the captured narration.

### Codebook promotion

`corpus-kb codebook promote` (`src/corpus_kb/cli/cli_coding.py`):

| Code | Meaning |
|------|---------|
| 0 | The code was promoted. |
| 3 | `duplicate_blocked` — a near-duplicate code exists. Resolve it first. |
| 1 | Any other failure. |

### The MCP stdio server

`corpus-kb --transport stdio` (`src/corpus_kb/mcp_server.py`):

| Code | Meaning |
|------|---------|
| 0 | Clean shutdown. The editor disconnected, or you pressed `Ctrl-C`. |
| 3 | Config or database failure at startup. |

The server blocks until the editor disconnects. stdout carries only the MCP
wire protocol. All logs go to stderr.

### The error taxonomy

One failure class maps to one HTTP status, one JSON-RPC socket code, and one
CLI exit code. The map lives in `src/corpus_kb/errors.py` (`ERROR_MAP`).

| Error type | HTTP status | Socket code | CLI exit |
|------------|-------------|-------------|----------|
| `config_error` | 400 | -32602 | 3 |
| `validation_error` | 400 | -32602 | 1 |
| `not_found` | 404 | -32001 | 1 |
| `conflict` | 409 | -32002 | 3 |
| `database_unavailable` | 503 | -32003 | 3 |
| `migration_missing` | 503 | -32004 | 3 |
| `dependency_missing` | 503 | -32005 | 3 |
| `internal_error` | 500 | -32603 | 1 |

Legacy exception class names (`ValueError`, `OSError`, `ConnectionError`,
asyncpg errors, and friends) map through the same table. Run this command to
regenerate the table above from the code:

```bash
python -c "from corpus_kb.errors import ERROR_MAP; [print(f'| \`{k}\` | {v[0]} | {v[1]} | {v[2]} |') for k, v in ERROR_MAP.items() if not k[0].isupper()]"
```

A table generated from `ERROR_MAP` cannot drift from `ERROR_MAP`.

### The doctor contract

`corpus-kb doctor` is read-only. It always exits 0 (`src/corpus_kb/_setup/install.py`,
`doctor_cmd`). The exit code says nothing about your system's health. Read the
report itself. `FAIL` lines need action; `OK` lines do not.

Honest status: `doctor` has no `--json` flag today. It has no fail-on-`FAIL`
flag today. Both are open work; do not script against them yet.

---

## Ports

| Port | What | Where it is set |
|------|------|-----------------|
| 8010 | HTTP API | `corpus-kb start --port` default, `server.port` in config, `CORPUS_KB_PORT` |
| 8011 | JSON-RPC socket, TCP, Windows only | Fixed in `src/corpus_kb/api/socket.py` |
| 5433 | Postgres, published by the compose stack | `compose.yaml` (`127.0.0.1:5433:5432`) |
| 5432 | Postgres, native install default | Your own Postgres server |

### The socket, by platform

On macOS and Linux the socket is a Unix domain socket at `/tmp/corpus-kb.sock`.
On Windows the code uses TCP on `127.0.0.1:8011` as a fallback for named pipes.
The port 8011 is fixed. It does not follow `--port` or `CORPUS_KB_PORT`.

### Postgres: 5433 (compose) vs 5432 (native)

Two supported setups. State which one you run before you debug a connection.

- **Compose path (default).** `corpus-kb setup` starts the bundled Postgres
  container. The container listens on 5432 inside. Compose publishes it on
  host port 5433. The packaged config already points at 5433, so it works
  out of the box. Use this path when Docker is present.
- **Native path (bring your own).** You run Postgres yourself. It usually
  listens on 5432. Set `CORPUS_KB_DATABASE_URL` to your server's URL. Use
  this path when Docker is absent or you have a managed Postgres.

The two URLs differ only in the port:

```text
postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb   # compose
postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb   # native
```

`corpus-kb doctor` honors `CORPUS_KB_DATABASE_URL` first. With it set,
doctor diagnoses that deployment, not the config file's.

---

## Vocabularies

### `source_type`, per surface

`source_type` means one thing on each surface. Do not mix them.

| Surface | `source_type` is | Values |
|---------|------------------|--------|
| `corpus-kb ingest` / `ingest_file` | A strategy override | Registered strategy names (below) |
| `ingest_text` / HTTP `/api/ingest/text` | A document type | `code`, `markdown`, `text`, `transcript` |
| `ingest-url` | Pinned for you | `web` |
| `research ingest` (dynamic ingest) | Detected per file | Derived from the suffix table below |

- File and directory ingest walk `WATCH_SUFFIXES`
  (`src/corpus_kb/ingest_watch.py`): `.py`, `.js`, `.ts`, `.tsx`, `.jsx`,
  `.rs`, `.go`, `.java`, `.cpp`, `.c`, `.h`, `.hpp`, `.rb`, `.php`, `.swift`,
  `.kt`, `.scala`, `.lua`, `.md`, `.markdown`, `.rst`, `.pptx`, `.txt`.
  Other suffixes are skipped.
- Text ingest accepts only the four document types above. Anything else is
  an error, not a fallback.
- `ingest-url` pins `source_type` to `web`. The `web_structure` strategy
  chunks each page by its headings.
- Research ingest (`corpus-kb research ingest`) accepts
  `SUPPORTED_SUFFIXES` (`src/corpus_kb/research/dynamic_ingest.py`):
  `.txt`, `.md`, `.vtt`, `.srt`, `.csv`, `.docx`. This is a smaller table
  than `WATCH_SUFFIXES`. Transcripts are the target modality there.

### Ingestion strategy names

Eight built-in strategies register in code
(`src/corpus_kb/ingestion/registry.py`):

| Name | Chunking behavior |
|------|-------------------|
| `default` | Unstructured partition, then the standard chunkers |
| `transcript` | Turn-aligned Q+A chunks for interviews and meetings |
| `pptx_structure` | One chunk per slide, plus speaker notes |
| `web_structure` | One chunk per page section |
| `survey` | CSV exports; one chunk per answer, question text in metadata |
| `markdown_sections` | Raw markdown segmented at heading boundaries |
| `code` (optional) | Tree-sitter AST chunks; needs the `[code]` extra |
| `textbook` (optional) | Windowed large-PDF parsing; needs the `[docling]` extra |

Third-party plugins register through the `corpus_kb.ingestion_strategies`
entry-point group. A broken plugin is logged and skipped. It never stops
ingest. The resolution order is: explicit `--source-type` override, then the
`ingestion.strategies` config map, then content detection, then
`ingestion.default` (`"default"`). Detection adopts a strategy only at
confidence 0.5 or higher.

---

## Tenancy

The default tenant UUID is
`00000000-0000-0000-0000-000000000001`
(`src/corpus_kb/contracts/model.py`, `DEFAULT_TENANT_ID`). Every surface
uses it unless you pass another `tenant_id`.

Row-level security is on. Every table policy compares `tenant_id` with the
`app.current_tenant_id` setting. Direct SQL must set it inside the same
transaction that reads or writes:

```sql
BEGIN;
SELECT set_config('app.current_tenant_id', '00000000-0000-0000-0000-000000000001', true);
SELECT count(*) FROM documents;
COMMIT;
```

The `set_config` third argument must be `true`. Without a transaction, the
setting does not stick. The code path for this is
`src/corpus_kb/storage/tenant_conn.py` (`tenant_connection`).

Pointers: `corpus-kb doctor` prints your environment-variable status. It
warns about set-but-unread `CORPUS_KB_*` variables. The full env-var table
lives in [ADMIN.md](ADMIN.md).

---

## Embedding model vs schema

The full matrix lives in [ADMIN.md](ADMIN.md) under "Embedding dimensions
and the vector schema". The contract in brief:

- The shipped schema stores `halfvec(2560)` — exactly 2560 dimensions.
- pgvector enforces the exact width. No padding. No truncation.
- `qwen3-embedding:4b` (2560d) is the shipped default and fits the column.
- Any other model width needs a schema migration first (migration 025 is
  the current regime).
- The ingest write path checks the column width first and raises a
  `config_error` that names the fix (`src/corpus_kb/tools/ingest_store.py`).
- `corpus-kb doctor` has an "embedding dimensions vs schema" check. It
  fails on a mismatch and prints the stored dimension histogram.

---

## The MCP surface

Editors connect over stdio. The command is:

```bash
corpus-kb --transport stdio
```

This serves the MCP tools generated from the same catalogue the CLI, HTTP,
and socket surfaces use (`src/corpus_kb/mcp_server.py`). Every catalogue
operation is reachable on all four surfaces: MCP, CLI,
HTTP, and socket. The `mcp-configs/` files for Claude Code, Cursor, and
Codex, plus the root `opencode.json`, launch this command. Tool names,
parameters, and envelopes match the other surfaces; the parity gate holds
all surfaces to one envelope per operation.

The live inventory of every operation is:

```bash
corpus-kb operations --indent 0
```

It lists each operation's surfaces, parameters with defaults, envelope
keys, and the error map. `--openapi` emits the HTTP spec from the same rows.
See [API.md](API.md) for worked HTTP examples and [FEATURES.md](FEATURES.md)
for the tool reference table.

### Default return k

`search` and `query` take an optional `k`. When a caller omits `k`, the
handler uses `reranking.return_top_k` (default `20`). An explicit `k`
always wins. The catalogue pins the same number. A lockstep test fails
if the config default and the catalogue default drift apart.

---

## Where to go next

- [INSTALL.md](INSTALL.md) — full setup, both Postgres paths
- [ADMIN.md](ADMIN.md) — config keys, env vars, schema, backups
- [API.md](API.md) — HTTP routes and examples
- [research.md](research.md) — cycle postures, gates, and resume rules
- [ROLLBACK_DRILL.md](ROLLBACK_DRILL.md) — the practiced backup drill
