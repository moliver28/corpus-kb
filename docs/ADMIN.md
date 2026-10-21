# Admin Guide

This page is for operators: configuration, schema, maintenance, and troubleshooting.

---

## Configuration reference

All tunable values live in `config.yaml` at the project root. Environment variables override some values in the file (see the table below). Every key listed here has a live consumer; dead keys are removed on sight.

### server

| Key | Default | Description |
|-----|---------|-------------|
| `transport` | `stdio` | `stdio`, `http`, or `socket` |
| `host` | `localhost` | HTTP server host |
| `port` | `8010` | HTTP server port |

### database

| Key | Default | Description |
|-----|---------|-------------|
| `backend` | `postgres` | Storage engine: `postgres` or `duckdb`. The loader refuses Postgres-only engines while `duckdb` is set |
| `connection_string` | `postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb` | Postgres URL |

### embedding

| Key | Default | Description |
|-----|---------|-------------|
| `provider` | `ollama` | Embedding provider (`ollama` or `pgml`). pgml cannot serve a 4B embedder |
| `fallback_provider` | `ollama` | Provider used when the primary fails |
| `model` | `qwen3-embedding:4b` | Model name. Must emit exactly 2560 dimensions — see "Embedding dimensions and the vector schema" |
| `base_url` | `http://localhost:11434` | Ollama API URL |
| `batch_size` | `32` | Embeddings per batch |
| `dimensions` | `2560` | Vector dimensions for the model. Must match the schema column for ingest to work |
| `mrl_trained` | `true` | Whether the model is Matryoshka-trained (gates the matryoshka arm and the dimensions passthrough) |
| `query_instruction` | `true` | Gates the query-side instruction prefix at `instruct()` |
| `late_chunking` | `true` | Late chunking for document ingest when the model supports it |

### storage

| Key | Default | Description |
|-----|---------|-------------|
| `vector_type` | `halfvec` | `chunks_vectors.embedding` column type. halfvec is required at 2560 dims: HNSW caps `vector` at 2000 dims, `halfvec` at 4000 |

### retrieval

| Key | Default | Description |
|-----|---------|-------------|
| `mode` | `hybrid_rrf` | Only supported value today; other values are rejected at load time |
| `vector_index` | `exact_scan` | DuckDB vector search mode: `exact_scan` or `hnsw`. Postgres keeps its pgvector HNSW and ignores this key |
| `fts_rebuild_debounce_s` | `2.0` | Seconds between DuckDB full-text index rebuilds. Positive numbers only |
| `vector_candidates` | `150` | Live vector-arm depth before fusion. Effective limit is `max(candidates, k * 2)` |
| `lexical_candidates` | `150` | Live FTS-arm depth before fusion. Same floor rule |
| `rrf_k` | `60` | Reciprocal Rank Fusion constant |
| `expand_to_parent` | `true` | Post-fusion small-to-big parent expansion. Fail-open |
| `exclude_degraded` | `true` | Vector search skips rows flagged degraded |
| `qa_context_in_fts` | `false` | Opt-in FTS index for transcript Q/A text |
| `exact_filter_selectivity_threshold` | `0.05` | Selectivity gate for exact-filter plans |
| `matryoshka_enabled` | `false` | Two-stage matryoshka retrieval (needs `embedding.mrl_trained`) |
| `matryoshka_dim` | `1024` | Front-slice width for the matryoshka arm |
| `candidate_multiplier` | `8` | Matryoshka candidate fan-out (candidates = k * multiplier) |
| `self_query.*` | (block) | Predicate parser sub-settings |
| `hnsw.*` | (block) | `m`/`ef_construction` (index DDL, mirrored by migration 025) plus the scan trio |

### reranking

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Score-based rerank stage (degrades honestly to the fused order) |
| `backend` | `null` | `null`/`generative` = qwen3 judgment scorer; `llamacpp` = true cross-encoder via llama-server; `fake` = test double |
| `model` | `qwen3-reranker:4b` | Rerank model |
| `llamacpp_url` | `http://localhost:8080` | `/v1/rerank` endpoint for backend `llamacpp` (llama-server default port) |
| `over_retrieve_n` | `60` | Rerank pre-fetch width for the fused candidates |
| `candidates` | `100` | Cap on results entering the reranker |
| `return_top_k` | `20` | The default return k: search/query callers that omit k get this value |
| `max_pair_tokens` | `768` | Document-side truncation per scored pair |
| `score_floor` | `0.15` | Drop results below the calibrated score |
| `calibration` | `minmax` | Score post-processing (`minmax` or `none`) |

#### Real cross-encoder reranking via llama-server

Ollama 0.31.1 has no rerank endpoint. It also refuses reranker GGUFs on
the generate route, so it cannot produce true cross-encoder scores. The
standard path is a rank-capable llama.cpp server:

1. Download a rank-pooling GGUF (for example `Qwen3-Reranker-0.6B-Q8_0.gguf`).
2. Start the server: `llama-server -m Qwen3-Reranker-0.6B-Q8_0.gguf --port 8080`.
3. Set `reranking.backend: llamacpp` in `config.yaml`, then restart corpus-kb.

The reranker then posts to `http://localhost:8080/v1/rerank` and returns
graded relevance scores. `corpus-kb doctor` probes the server under
"reranker backend". Without the server, the stage falls open to the fused
RRF order.

Legacy namespaces: `search.*`, `ingest.hierarchy.*`, and `contextual.*`
still work as aliases. Canonical keys win; alias use logs a deprecation
warning.

### topics

The Wave 4 topic layer (`src/corpus_kb/topics/`, docs/TOPICS.md). Ships
dormant; the BERTopic import needs the `[topics]` extra.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Feature flag. `topics-build` answers with an honest no-op while off |
| `method` | `bertopic` | Only `bertopic` exists; unknown values are rejected at load |
| `llm_reduction` | `true` | LLM labels (and merges) on top of the c-TF-IDF labels; failures keep the c-TF-IDF labels |
| `min_topic_size` | `auto` | `"auto"` = the BERTopic default (10, reported); or a positive int |
| `llm` | `qwen3:4b` | The reduction model |
| `llm_base_url` | `http://localhost:11434` | Ollama host for the reduction step |
| `fixture_dir` | `tests/fixtures/topics_recorded` | Recorded reduction answers (SHA256-keyed) |
| `live_fallback` | `false` | Allow live reduction calls when no fixture exists |

### graph

| Key | Default | Description |
|-----|---------|-------------|
| `backend` | `age` | Graph store backend (`age` or `postgres`) |
| `max_hops` | `5` | Graph traversal depth cap. Positive int, 1 to 25; the ceiling matches the AGE cap |
| `extractor` | `pgml` | LEGACY alias for `extraction.backend`. Accepted forever; `extraction.backend` wins when both are set |
| `extract_entities` | `true` | Enable entity extraction during ingest |
| `ontology_path` | `config/ontology.yaml` | Ontology vocabulary file |
| `fixture_dir` | `tests/fixtures/langextract_recorded` | Recorded fixtures for offline runs |
| `live_fallback` | `false` | Allow live LLM calls when no fixture exists |
| `model_version` | `langextract-default` | Extraction provenance tag stored on relations |
| `prompt_version` | `v1` | Extraction prompt provenance tag |

### extraction

| Key | Default | Description |
|-----|---------|-------------|
| `backend` | `pgml` | PREFERRED extraction-backend key: `regex`, `langextract`, `pgml`, or `gliner` |
| `gliner.enabled` | `false` | Opt-in gate for the GLiNER2 backend |
| `gliner.model` | `fastino/gliner2-large-v1` | GLiNER2 model |
| `gliner.revision` | `main` | Model revision |
| `gliner.types` | `[person, organization, location, date, money, product]` | Mention-level types |

### research

Research-domain blocks (transcript ingestion, inductive engine, embedder
promotion guard). Determinism pins (UMAP seeds, scoring batch size) are CODE
CONSTANTS on purpose — only governance knobs live here.

#### research.embedder

| Key | Default | Description |
|-----|---------|-------------|
| `dimensions` | `1024` | Promotion guard: non-1024 winners HALT, never silent-pad |

The embedding strategy (G1 winner: naive-prefix) and the `"1024"` row
revision tag are fixed in code — deliberately not configuration.

#### research.ingest

| Key | Default | Description |
|-----|---------|-------------|
| `watch_interval_s` | `10` | `corpus-kb research ingest --watch` poll interval (seconds) |

#### research.inductive

| Key | Default | Description |
|-----|---------|-------------|
| `entropy_threshold` | `0.85` | Normalized soft-entropy gate for the LLM meta-decision |
| `recluster_every_batches` | `8` | Full re-cluster cadence (v5 §9.5: every 5-10 batches) |
| `centroid_drift_threshold` | `0.15` | Mean cosine drift between snapshots triggering early refresh |
| `tau_dup` | `0.85` | Promote-time duplicate gate (calibrated from gold when available) |

### installer

| Key | Default | Description |
|-----|---------|-------------|
| `postgres_image` | `ghcr.io/moliver28/corpus-kb-postgres:0.1.0-pg17` | Postgres image for `corpus-kb setup` |
| `data_dir` | `~/.corpus-kb` | Installer checkpoint + data directory |
| `verify_image_signature` | `true` | Verify the Postgres image signature (cosign) before pull |
| `profiles.*` | (block) | Hardware profiles (minimal / balanced / performance) |

### Environment variables

Every `CORPUS_KB_*` variable the code reads. `corpus-kb doctor` warns when a set variable is NOT in this table. That variable is dead for this version: remove it, or set the config key instead.

| Variable | Overrides / feeds | Consumed by |
|----------|-------------------|-------------|
| `CORPUS_KB_CONFIG` | config discovery: absolute path to the config file | `config.load_config` |
| `CORPUS_KB_DATABASE_URL` | `database.connection_string` | `load_config`, `server_wiring`, `domain.application`, `_setup.migrate`, `_setup.install`, `_setup.validate_migration` |
| `CORPUS_KB_DATABASE_BACKEND` | `database.backend` | `load_config` |
| `CORPUS_KB_EMBEDDING_MODEL` | `embedding.model` | `load_config` |
| `CORPUS_KB_EMBEDDING_DIMENSIONS` | `embedding.dimensions` | `load_config` |
| `CORPUS_KB_GRAPH_BACKEND` | `graph.backend` | `load_config` |
| `CORPUS_KB_TRANSPORT` | `server.transport` | `load_config` |
| `CORPUS_KB_PORT` | `server.port` | `load_config` |
| `CORPUS_KB_OPENAI_COMPAT` | `server.openai_compat` | `load_config` |
| `CORPUS_KB_OTLP_ENDPOINT` | `observability.otlp_endpoint` | `load_config` |
| `CORPUS_KB_INSTALL_DATA_DIR` | `installer.data_dir` | installer checkpoint state (`_setup.install_state`) |
| `CORPUS_KB_INSTALL_POSTGRES_IMAGE` | `installer.postgres_image` | `corpus-kb setup` image pull/verify (`_setup.install`) |
| `CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE` | `installer.verify_image_signature` | `corpus-kb setup` image verify (`_setup.install`) |
| `CORPUS_KB_SNAPSHOT_PERIOD` | (no config key; aggregate snapshot interval, default 100) | `domain.application` |
| `CORPUS_KB_TEST_DB` | (no config key; throwaway test DB name, default `corpus_kb_t11_test`) | `tests/research_db.py` |

### Embedding dimensions and the vector schema

The vector column `chunks_vectors.embedding` is a FIXED `halfvec(2560)` typmod (migration 025). pgvector enforces the exact width — verified on pgvector 0.8.x / Postgres 16:

| Model | Dimensions | Can ingest into the shipped schema? |
|-------|------------|-------------------------------------|
| `qwen3-embedding:4b` (shipped default) | 2560 | YES — matches the column |
| `nomic-embed-text` | 768 | NO — every vector insert fails with SQLSTATE 22000 |
| `qwen3-embedding:8b-q8_0` | 4096 | NO — needs a schema migration first |

halfvec is required at this width: pgvector HNSW caps `vector` at 2000
dimensions and `halfvec` at 4000. The 2560-dim column carries a real HNSW
index (`halfvec_cosine_ops`, m = 16, ef_construction = 128). The doctor
warns when `retrieval.hnsw.m`/`ef_construction` drift from the live index
options.

Vectors are never padded and never truncated. Three guards make this honest:

1. **At ingest**: the write path probes the column width first. A mismatch raises a typed `config_error`.
2. The fix: point `embedding.model` at a model emitting 2560 dimensions. Set `embedding.dimensions` to 2560. Then re-ingest.
3. **At the embedder**: the first embed response is width-checked. A wrong-width model raises a `config_error` with the native-width fix.
4. **In doctor**: the dimension check compares the config with the column typmod. It reports the stored per-row histogram. FAIL on mismatch.

A model switch invalidates all stored embeddings. Migration 025 retypes the
column; old rows become stale (`embedding IS NULL`). `corpus-kb
ingest-reembed` rebuilds stale rows, not only degraded-flagged rows.

---

## Database schema

The Postgres schema has 12 projection tables plus supporting tables for eventsourcing, tags, metadata, idempotency, and RLS.

```mermaid
erDiagram
    tenants ||--o{ documents : has
    documents ||--o{ chunks : contains
    chunks ||--|| chunks_vectors : embeds
    documents ||--o{ entities : extracted
    entities ||--o{ relations : connects
    documents ||--o{ document_tags : tagged
    tags ||--o{ document_tags : used
    documents ||--o{ metadata : stores
```

### Migrations

Schema changes are managed through idempotent SQL migrations in `src/corpus_kb/migrations/`. The migration runner (`src/corpus_kb/_setup/migrate.py`) tracks applied migrations in `corpus.schema_migrations` and runs each unapplied file inside a transaction.

```bash
# Run migrations. Native-Postgres path (port 5432). The bundled compose
# stack publishes 5433 instead — see OPERATIONS.md, "Ports".
export CORPUS_KB_DATABASE_URL=postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
python src/corpus_kb/_setup/migrate.py

# Or use the installer (compose path; no export needed)
corpus-kb setup
```

Re-running is safe — already-applied migrations are skipped.

### Projection tables

| Table | Purpose |
|-------|---------|
| `tenants` | Multi-tenant placeholder; default tenant only today |
| `documents` | Document metadata: source, type, chunk count, hash |
| `chunks` | Text chunks with provenance and full-text index |
| `chunks_vectors` | pgvector embeddings for each chunk |
| `entities` | Knowledge graph nodes |
| `relations` | Knowledge graph edges |
| `tags` | Colored labels |
| `document_tags` | Many-to-many mapping |
| `metadata` | Key-value store per document or tenant |
| `projection_checkpoints` | Catch-up subscription state |
| `projection_dlq` | Failed projection events |
| `idempotency_keys` | Command deduplication |

The eventsourcing library owns `event_store` and `snapshot_store` automatically.

### Row-level security

Every table enables RLS and has a policy that checks `current_setting('app.current_tenant_id', true)::UUID`. Handlers set this with `set_config(..., is_local=true)` inside the same transaction (see `src/corpus_kb/storage/tenant_conn.py`). The default tenant UUID is `00000000-0000-0000-0000-000000000001`; direct SQL must set it the same way — see [OPERATIONS.md](OPERATIONS.md), "Tenancy".

---

## Multi-tenancy

Today Corpus-KB is single-tenant with the infrastructure already in place:

- All tables carry `tenant_id`.
- RLS policies enforce tenant isolation.
- Handlers pass `tenant_id` from the request into `SET LOCAL app.current_tenant_id`.

To add real multi-tenancy, create tenants in the `tenants` table and route each request to the right tenant context. Vector search is also tenant-isolated through `chunks_vectors.tenant_id`.

---

## DuckDB engine notes

DuckDB is the opt-in local engine (`database.backend: duckdb`). While an
ingest runs, a few read-time behaviors differ from Postgres. All of them
settle when the ingest commit lands.

- Chunk vectors and relations write in a second pass. A read during an
  ingest can see live chunks with no vectors yet, or relations that are
  one event behind. One transaction cannot span both write passes.
- A shrunken document updates its surviving chunks and tombstones the
  rest. The tombstones become visible at the commit, not one by one.
- A pipeline-carried document date lands on the first insert only. A
  changed date on a re-ingest updates Postgres; DuckDB keeps the first.
- `documents.chunk_count` stays 0 in the DuckDB table (one insert, no
  later update). Document reads compute the real count from `chunks`.

---

## Backup and restore

Use standard Postgres tools.

```bash
# Backup everything (schema + data). Port 5432 = native Postgres;
# the compose stack publishes 5433 (see OPERATIONS.md, "Ports").
pg_dump -d postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb \
  -F c -f corpus_kb_backup.dump

# Restore into a fresh database
pg_restore -d postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_new \
  corpus_kb_backup.dump
```

The event store is included in the backup, so you can replay projections on restore.

---

## Monitoring

- Check `projection_dlq` for failed projection events.
- Check `projection_checkpoints` to see how far each projection has caught up.
- Use `GET /api/stats` or the MCP `get_stats` tool for counts.
- Watch the server logs for embedding failures, which degrade gracefully but leave zero vectors.

---

## CI/CD pipeline

The CI workflow runs on every pull request and push to `master`:

1. **lint (ruff)** - syntax and format checks
2. **type-check (pyright)** - static type checking
3. **validate-configs** - validates MCP config files
4. **test** - runs `pytest tests/test_ingest.py` on Ubuntu, Windows, and macOS

Additional governance workflows check branch naming, commit messages, and agent governance rules.

See `.github/workflows/` for the exact definitions.

---

## Troubleshooting

### Server fails to start with `No database connection string`

Set `CORPUS_KB_DATABASE_URL` or add `database.connection_string` to `config.yaml`.

### `relation "documents" does not exist`

Run migrations (`python src/corpus_kb/_setup/migrate.py`) or load the schema SQL manually (`psql -f src/corpus_kb/migrations/001_corpus_schema.sql`).

### Vector search returns empty results

- Verify Ollama is running and the model is pulled.
- Check that `chunks_vectors` rows exist and have non-null `embedding` values. Stale rows (`embedding IS NULL`) rebuild via `corpus-kb ingest-reembed`.
- Verify `embedding.dimensions` matches the embedding column size.

### Projections fall behind

Check `projection_checkpoints` and `projection_dlq`. Restart the server to resume catch-up subscriptions.

### RLS seems to return no rows

Confirm the tenant context is set. Querying directly with `psql` without setting `app.current_tenant_id` will return no rows.

---

## Next steps

- [Install guide](INSTALL.md)
- [Features overview](FEATURES.md)
- [API reference](API.md)
- [Development guide](DEVELOPMENT.md)
- [FAQ](FAQ.md)
