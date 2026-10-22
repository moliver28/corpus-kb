# Install Corpus-KB

This guide takes you from a clean machine to a running Corpus-KB server. The current release targets **PostgreSQL 17** with **pgvector** and **Apache AGE**, **Python 3.11 or newer**, and **Ollama** for local embeddings.

All commands assume you are at the repo root.

## Two database paths: 5433 (compose) vs 5432 (native)

Pick one before you start. Ports differ, and the port tells you which path is live.

- **Compose path (default).** You have Docker. Run `corpus-kb setup`. It
  starts the bundled Postgres container and loads the schema. The container
  listens on 5432 inside; compose publishes it on host port **5433**. The
  packaged config already points at 5433, so it works unchanged. The manual
  steps in Step 1 and Step 2 below are then NOT needed.
- **Native path.** You have no Docker, or you run your own Postgres. It
  usually listens on **5432**. Follow Step 1 and Step 2, then set
  `CORPUS_KB_DATABASE_URL` so the runtime and `corpus-kb doctor` target
  your server.

The full contract (ports, exit codes, vocabularies) lives in
[OPERATIONS.md](OPERATIONS.md).

## No Docker? The DuckDB path

One command, no containers, no Postgres. Corpus-KB lives in one local
file, and the event store sits beside it:

```bash
corpus-kb setup --backend duckdb
```

The run does five things: installs the `duckdb` extra if it is missing,
creates the analytics file and applies its migrations, creates the
SQLite event store (`<file>.events.db`), pre-installs the `fts`
extension, and writes the config pair into `config.yaml`. One
confirmation covers the run. Re-running is safe.

Pick the file location with `--dsn` (a bare path or a `duckdb:///` DSN):

```bash
corpus-kb setup --backend duckdb --dsn C:/data/kb.duckdb
corpus-kb setup --backend duckdb --dry-run   # preview every step
```

Then check the install with `corpus-kb doctor` and start the server
with `corpus-kb start`. Exact-scan search stays fast to about 100k
chunks; above that, the Postgres path fits better. The doctor prints
this size hint for your corpus. See [ADMIN.md](ADMIN.md), "DuckDB
backend: setup and doctor", for the full check list.

Ollama is still the embedder: install it and pull your embedding model
as in Step 5.

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
3. pgvector and AGE Windows builds are available from their respective GitHub releases. Install them into the Postgres extension directory, or use the community Windows package.

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

-- PostgreSQL 15+ revoked CREATE on the public schema from non-owners by
-- default (PG 14 and older granted it to PUBLIC). Without this grant the
-- first event-store write dies with "permission denied for schema public".
GRANT CREATE ON SCHEMA public TO corpus_user;

-- Future objects created by migrations and the event store get the same
-- privileges automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO corpus_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO corpus_user;
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

All remaining commands in this guide run from the repo root.

`[dev]` installs pytest and related tooling. If you want the optional GraphQLite graph backend, add `graphqlite`:

```bash
pip install -e ".[graphqlite,dev]"
```

The browser pages and the notebooks console are separate extras:

```bash
pip install -e ".[ui,notebook,dev]"
```

Without them the server runs headless and logs one line per missing extra.

---

## Step 4: Load the schema

The easiest way is the compose path: `corpus-kb setup` starts the bundled
Postgres and runs every migration. Skip to Step 5.

On the native path, use the migration runner with your server's URL
(native Postgres listens on 5432):

```bash
export CORPUS_KB_DATABASE_URL=postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
python src/corpus_kb/_setup/migrate.py
```

Migrations are idempotent — re-running is a no-op. They are tracked in `corpus.schema_migrations`.

Alternatively, load the schema SQL manually:

```bash
psql -d postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb \
  -f src/corpus_kb/migrations/001_corpus_schema.sql
```

This creates the projection tables plus tenants, event-sourcing checkpoint, DLQ, idempotency, tags, and metadata tables. It also enables RLS policies and inserts the default tenant placeholder.

### Using the installer

You can also let the installer handle database creation and migrations:

```bash
corpus-kb doctor      # read-only diagnostics
corpus-kb setup       # guided setup with per-step confirmation
```

The installer detects your hardware profile and creates the database if needed. It runs migrations, pulls the recommended Ollama model, and writes its config file under `~/.corpus-kb`.

The root `scripts/install.py` file still exists as a deprecation shim: it warns and delegates to `corpus_kb._setup.install`.

---

## Step 5: Install and start Ollama

Download Ollama from [ollama.com](https://ollama.com) and start it.

One rule decides the model: **the schema column is fixed at 2560 dimensions,
and pgvector needs an exact match.** The bundled schema stores `halfvec(2560)`.
Pull the model that fits it:

```bash
ollama pull qwen3-embedding:4b     # 2560 dimensions — the shipped default
```

`qwen3-embedding:4b` is the shipped config default and the installer's
recommendation for every profile. Any other model width needs a schema
migration first; ingest raises a `config_error` that names the fix. Two
guards catch a mismatch early — the ingest write path and the `corpus-kb
doctor` "embedding dimensions vs schema" check. The full model↔schema matrix
lives in [ADMIN.md](ADMIN.md); the guard behavior is summarized in
[OPERATIONS.md](OPERATIONS.md).

---

## Step 6: Configure Corpus-KB

Create or edit `config.yaml` at the repo root. The connection string below
targets the **compose** path (host port 5433). On the **native** path, use
port 5432 instead — or skip the file edit and export
`CORPUS_KB_DATABASE_URL`:

```yaml
server:
  transport: http
  host: localhost
  port: 8010

database:
  # compose path (default): the bundled container publishes 5433.
  # native path: change 5433 to 5432.
  connection_string: "postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb"

embedding:
  provider: ollama
  # The shipped schema stores halfvec(2560). Use the matching model.
  model: qwen3-embedding:4b
  dimensions: 2560
  base_url: http://localhost:11434
  batch_size: 32

search:
  rrf_k: 60

graph:
  backend: postgres
  ontology_path: config/ontology.yaml

extraction:
  backend: langextract
```

Every key above has a live consumer. The full reference (including keys this
sample omits) is [ADMIN.md](ADMIN.md).

You can also use environment variables. These override some values in `config.yaml` — the definitive table lives in [ADMIN.md](ADMIN.md):

| Variable | Maps to |
|----------|---------|
| `CORPUS_KB_DATABASE_URL` | `database.connection_string` |
| `CORPUS_KB_EMBEDDING_MODEL` | `embedding.model` |
| `CORPUS_KB_EMBEDDING_DIMENSIONS` | `embedding.dimensions` |
| `CORPUS_KB_GRAPH_BACKEND` | `graph.backend` |
| `CORPUS_KB_TRANSPORT` | `server.transport` |
| `CORPUS_KB_PORT` | `server.port` |

`corpus-kb doctor` honors the same precedence as the runtime (env wins over
config), so with `CORPUS_KB_DATABASE_URL` set it diagnoses THAT deployment.

---

## Step 7: Start the server

HTTP mode (starts HTTP + JSON-RPC socket + projections):

```bash
corpus-kb start --transport http --port 8010
```

MCP stdio mode for editor agents: `corpus-kb --transport stdio`.
The server registers every catalogue operation as an MCP tool and serves
them over stdin/stdout. The config files in `mcp-configs/` and the root
`opencode.json` launch exactly this command. HTTP stays available side
by side (see [API.md](API.md)).

With the `[ui]` extra installed, the same command serves the browser
pages at `http://localhost:8010` (see [UI.md](UI.md)). With `[notebook]`,
marimo notebooks appear at `/notebooks` (see [NOTEBOOKS.md](NOTEBOOKS.md)).

---

## Step 8: Ingest and search

Ingest a file:

```bash
curl -X POST http://localhost:8010/api/ingest/file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "src/corpus_kb/server_wiring.py"}'
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

### `permission denied for schema public`

PostgreSQL 15+ no longer grants CREATE on `public` to ordinary roles. As the
superuser, on the `corpus_kb` database, run:

```sql
GRANT CREATE ON SCHEMA public TO corpus_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO corpus_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO corpus_user;
```

The setup and research commands print this exact remediation when they hit
the permission wall.

### pgvector or AGE extension missing

Re-run `CREATE EXTENSION` as a superuser on the `corpus_kb` database.

### `relation "documents" does not exist`

Load the schema via migrations (`python src/corpus_kb/_setup/migrate.py`) or manually (`psql -f src/corpus_kb/migrations/001_corpus_schema.sql`) before starting the server.

### Ollama connection errors

The server starts without Ollama, but search falls back to full-text only until Ollama is available. Check that `ollama serve` is running and reachable at the configured `base_url`.

### Port already in use

Change `--port` or set `CORPUS_KB_PORT`. The JSON-RPC socket uses port `8011` on Windows.

---

## Next steps

- Read the [Features overview](FEATURES.md)
- Browse the [API reference](API.md)
- Learn how to operate the system in [Admin](ADMIN.md)
- Ports, exit codes, vocabularies, tenancy: [Operations](OPERATIONS.md)
- The novice path in the browser: [UI.md](UI.md)
