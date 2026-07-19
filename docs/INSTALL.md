# Install Corpus-KB

This guide takes you from a clean machine to a running Corpus-KB server. The current release targets **PostgreSQL 17** with **pgvector** and **Apache AGE**, **Python 3.11 or newer**, and **Ollama** for local embeddings.

---

## Prerequisites

- PostgreSQL 17+ with pgvector and Apache AGE extensions
- Python 3.11+
- Ollama

---

## Step 1: Install PostgreSQL 17

### macOS

```bash
brew install postgresql@17
brew services start postgresql@17
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y postgresql-17 postgresql-17-pgvector postgresql-17-age
sudo systemctl start postgresql
```

### Windows

1. Download the installer from [postgresql.org/download/windows](https://www.postgresql.org/download/windows/).
2. Run the installer and note the superuser password.
3. pgvector and AGE Windows builds are available from their respective GitHub releases; install them into the Postgres extension directory or use the Windows package maintained by the community.

### Docker (any platform)

```bash
docker run -d --name corpus-postgres \
  -e POSTGRES_USER=corpus_user \
  -e POSTGRES_PASSWORD=corpus_pass \
  -e POSTGRES_DB=corpus_kb \
  -p 5432:5432 \
  pgvector/pgvector:pg17
```

Apache AGE inside Docker requires a custom image or installing the AGE extension after the container starts. The community AGE image is `apache/age:release_PG17`.

---

## Step 2: Create the database and user

Connect as the `postgres` superuser and run:

```sql
CREATE DATABASE corpus_kb;
CREATE USER corpus_user WITH PASSWORD 'corpus_pass';
GRANT ALL PRIVILEGES ON DATABASE corpus_kb TO corpus_user;

\c corpus_kb

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- For Apache AGE:
CREATE EXTENSION IF NOT EXISTS age;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO corpus_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO corpus_user;
```

Your connection string will be:

```
postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
```

---

## Step 3: Clone and install Corpus-KB

```bash
git clone https://github.com/moliver28/corpus-kb.git
cd corpus-kb
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

`[dev]` installs pytest and related tooling. If you want the optional GraphQLite graph backend, add `graphqlite`:

```bash
pip install -e ".[graphqlite,dev]"
```

---

## Step 4: Load the schema

The easiest way is to use the migration runner:

```bash
cd corpus-kb
export CORPUS_KB_DATABASE_URL=postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
python scripts/migrate.py
```

Migrations are idempotent — re-running is a no-op. They are tracked in `corpus.schema_migrations`.

Alternatively, load the schema SQL manually:

```bash
psql -d postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb \
  -f corpus-kb/migrations/001_corpus_schema.sql
```

This creates the projection tables plus tenants, event-sourcing checkpoint, DLQ, idempotency, tags, and metadata tables. It also enables RLS policies and inserts the default tenant placeholder.

### Using the installer

You can also let the installer handle database creation and migrations:

```bash
cd corpus-kb
python scripts/install.py doctor      # read-only diagnostics
python scripts/install.py install --apply   # guided setup with per-step confirmation
```

The installer detects your hardware profile, creates the database if needed, runs migrations, pulls the recommended Ollama model, and writes a config file to `~/.corpus-kb/config.yaml`.

---

## Step 5: Install and start Ollama

Download Ollama from [ollama.com](https://ollama.com) and start it. Then pull an embedding model:

```bash
ollama pull nomic-embed-text
```

`nomic-embed-text` is the default: about 274 MB, 768 dimensions, and fast on CPU. For higher quality, pull `qwen3-embedding:8b-q8_0` and update `config.yaml` to `dimensions: 4096`.

---

## Step 6: Configure Corpus-KB

Create or edit `config.yaml` in the `corpus-kb/` directory:

```yaml
server:
  name: corpus-kb
  transport: http
  host: localhost
  port: 8010

database:
  connection_string: "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"

embedding:
  provider: ollama
  model: nomic-embed-text
  base_url: http://localhost:11434
  batch_size: 32
  dimensions: 768

chunking:
  max_size: 4096
  overlap: 200

search:
  rrf_k: 60
  expand_context: true
  index_type: hnsw

graph:
  backend: postgres
  extractor: langextract
  ontology_path: config/ontology.yaml
```

You can also use environment variables. These override any value in `config.yaml`:

| Variable | Maps to |
|----------|---------|
| `CORPUS_KB_DATABASE_URL` | `database.connection_string` |
| `CORPUS_KB_EMBEDDING_MODEL` | `embedding.model` |
| `CORPUS_KB_EMBEDDING_DIMENSIONS` | `embedding.dimensions` |
| `CORPUS_KB_GRAPH_BACKEND` | `graph.backend` |
| `CORPUS_KB_TRANSPORT` | `server.transport` |
| `CORPUS_KB_PORT` | `server.port` |

---

## Step 7: Start the server

HTTP mode (starts HTTP + JSON-RPC socket + projections):

```bash
python -m src.server_wiring --transport http --port 8010
```

MCP stdio mode for editor agents:

```bash
corpus-kb --transport stdio
```

---

## Step 8: Ingest and search

Ingest a file:

```bash
curl -X POST http://localhost:8010/api/ingest/file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "src/server_wiring.py"}'
```

Search:

```bash
curl -X POST http://localhost:8010/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "how does startup work"}'
```

Run SQL:

```bash
curl -X POST http://localhost:8010/api/query/sql \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT source_type, COUNT(*) FROM documents GROUP BY source_type"}'
```

---

## Troubleshooting

### `No database connection string` on startup

Set `CORPUS_KB_DATABASE_URL` or add `database.connection_string` to `config.yaml`.

### pgvector or AGE extension missing

Re-run `CREATE EXTENSION` as a superuser on the `corpus_kb` database.

### `relation "documents" does not exist`

Load the schema via migrations (`python scripts/migrate.py`) or manually (`psql -f corpus-kb/migrations/001_corpus_schema.sql`) before starting the server.

### Ollama connection errors

The server starts without Ollama, but search falls back to full-text only until Ollama is available. Check that `ollama serve` is running and reachable at the configured `base_url`.

### Port already in use

Change `--port` or set `CORPUS_KB_PORT`. The JSON-RPC socket uses port `8011` on Windows.

---

## Next steps

- Read the [Features overview](FEATURES.md)
- Browse the [API reference](API.md)
- Learn how to operate the system in [Admin](ADMIN.md)
