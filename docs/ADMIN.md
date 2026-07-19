# Admin Guide

This page is for operators: configuration, schema, maintenance, and troubleshooting.

---

## Configuration reference

All tunable values live in `config.yaml` at the project root. Environment variables override any value in the file.

### server

| Key | Default | Description |
|-----|---------|-------------|
| `name` | `corpus-kb` | Server name |
| `transport` | `stdio` | `stdio`, `http`, or `sse` |
| `host` | `localhost` | HTTP server host |
| `port` | `8010` | HTTP server port |

### database

| Key | Default | Description |
|-----|---------|-------------|
| `connection_string` | (none) | Postgres URL. Required. |

### embedding

| Key | Default | Description |
|-----|---------|-------------|
| `provider` | `ollama` | Embedding provider |
| `model` | `nomic-embed-text` | Ollama model name |
| `base_url` | `http://localhost:11434` | Ollama API URL |
| `batch_size` | `32` | Embeddings per batch |
| `dimensions` | `768` | Vector dimensions (must match model) |

### chunking

| Key | Default | Description |
|-----|---------|-------------|
| `max_size` | `4096` | Max chunk size in characters |
| `overlap` | `200` | Characters to overlap between chunks |

### search

| Key | Default | Description |
|-----|---------|-------------|
| `rrf_k` | `60` | Reciprocal Rank Fusion constant |
| `expand_context` | `true` | Include surrounding chunks by default |

### graph

| Key | Default | Description |
|-----|---------|-------------|
| `extractor` | `regex` | `regex` or `langextract` |
| `extract_entities` | (true) | Enable entity extraction |
| `ontology_path` | `config/ontology.yaml` | Ontology vocabulary file |
| `fixture_dir` | `tests/fixtures/langextract_recorded` | Recorded fixtures for tests |
| `live_fallback` | `false` | Allow live LLM calls when no fixture exists |

### Environment variables

| Variable | Overrides |
|----------|-----------|
| `CORPUS_KB_DATABASE_URL` | `database.connection_string` |
| `CORPUS_KB_EMBEDDING_MODEL` | `embedding.model` |
| `CORPUS_KB_EMBEDDING_DIMENSIONS` | `embedding.dimensions` |
| `CORPUS_KB_GRAPH_BACKEND` | `graph.backend` |
| `CORPUS_KB_TRANSPORT` | `server.transport` |
| `CORPUS_KB_PORT` | `server.port` |

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

Schema changes are managed through idempotent SQL migrations in `corpus-kb/migrations/`. The migration runner (`scripts/migrate.py`) tracks applied migrations in `corpus.schema_migrations` and runs each unapplied file inside a transaction.

```bash
# Run migrations
export CORPUS_KB_DATABASE_URL=postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
python scripts/migrate.py

# Or use the installer
python scripts/install.py install --apply
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

Every table enables RLS and has a policy that checks `current_setting('app.current_tenant_id', true)::UUID`. Handlers set this with `SET LOCAL` on each connection. The default tenant UUID is `00000000-0000-0000-0000-000000000001`.

---

## Multi-tenancy

Today Corpus-KB is single-tenant with the infrastructure already in place:

- All tables carry `tenant_id`.
- RLS policies enforce tenant isolation.
- Handlers pass `tenant_id` from the request into `SET LOCAL app.current_tenant_id`.

To add real multi-tenancy, create tenants in the `tenants` table and route each request to the right tenant context. Vector search is also tenant-isolated through `chunks_vectors.tenant_id`.

---

## Backup and restore

Use standard Postgres tools.

```bash
# Backup everything (schema + data)
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

Run migrations (`python scripts/migrate.py`) or load the schema SQL manually (`psql -f corpus-kb/migrations/001_corpus_schema.sql`).

### Vector search returns empty results

- Verify Ollama is running and the model is pulled.
- Check that `chunks_vectors` rows exist and have non-null `vector` values.
- Verify `embedding.dimensions` matches the vector column size.

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
