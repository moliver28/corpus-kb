# Storage backends: Postgres and DuckDB

Corpus-KB runs on two storage engines. Postgres is the default. DuckDB is the local single-user engine. One config key selects the engine. `corpus-kb migrate-data` moves a corpus between them. No re-ingest.

## Contents

- [Quickstart](#quickstart)
- [Which engine fits your corpus](#which-engine-fits-your-corpus)
- [Operating DuckDB](#operating-duckdb)
- [Module map](#module-map)
- [Keep the two schemas in step](#keep-the-two-schemas-in-step)
- [Declared differences between the engines](#declared-differences-between-the-engines)
- [Move a corpus with migrate-data](#move-a-corpus-with-migrate-data)
- [Backup and restore](#backup-and-restore)

Both engines give you the same product. Every operation returns the same envelope on both. The small engine differences are declared below and pinned by tests.

---

## Quickstart

### DuckDB: one file, no Docker

DuckDB suits one person running one server on a local corpus.

```bash
pip install -e ".[duckdb]"
corpus-kb setup --backend duckdb
corpus-kb start
```

`setup` writes the config pair, applies the schema, and creates the database file. The default file is `./corpus.duckdb`. The command also creates the event-store sidecar and installs the `fts` extension.

Place the file anywhere with `--dsn`. A bare path or a `duckdb:///` DSN both work:

```bash
corpus-kb setup --backend duckdb --dsn C:/data/kb.duckdb
```

Re-running `setup` is safe. Preview the steps first with `--dry-run`.

### Postgres: the default engine

Postgres suits shared corpora, larger scale, and high availability.

```bash
pip install -e .
corpus-kb setup
corpus-kb start
```

`setup` starts the bundled Postgres container, applies migrations, and checks the extensions. The full platform walk-through lives in [INSTALL.md](INSTALL.md).

---

## Which engine fits your corpus

| Factor | DuckDB | Postgres |
|---|---|---|
| Deployment | One file, no Docker | Compose stack, or your own server |
| Writers | One process opens the file read-write | Many clients, RLS-isolated |
| Corpus size | Up to about 100k chunks | Beyond that; the HNSW index keeps search fast |
| Vector search | Exact scan, always exact results | pgvector HNSW, fast approximate results |
| Graph engine | Recursive CTEs, or opt-in DuckPGQ | Apache AGE openCypher, or CTEs |
| In-database ML | None; `pgml` selections are refused | PostgresML embeddings, rerank, and NER |
| Tenancy | Single user, `tenant_id` predicates | Row-level security plus the same predicates |

The exact-scan ceiling is about 100k chunks at the shipped 2560 dimensions. Committed benchmark numbers live in [docs/research/benchmarks](research/benchmarks/README.md).

`corpus-kb doctor` prints the active engine in its "Storage backend" section. It suggests the other engine when your corpus size points there. A suggestion is never a warning; both engines are fully supported.

---

## Operating DuckDB

### Checkpoints and the write-ahead log

DuckDB writes changes to `<file>.wal` first. It folds that log into the database file when the log passes 16 MiB (`checkpoint_threshold`). A clean shutdown checkpoints too. Copy the `.wal` file together with the `.duckdb` file. Or stop the server first; then the `.duckdb` file alone is current.

### Commits and fsync

Every commit fsyncs the write-ahead log. Many tiny autocommit writes pay that cost per row. The ingest pipeline already batches work into transactions. Keep your own scripts batched too.

### The progress bar

DuckDB shows a progress bar when no terminal is attached. Server code turns it off after connect (`storage/duckdb_gateway.py`, `storage/graph_factory.py`). Leave it off; the bar only adds log noise.

### Memory

DuckDB caps query memory (`memory_limit`) and spills to disk under it. Large blocking operators at scale are its documented failure mode. Stay inside the ~100k-chunk ceiling; grow into Postgres instead.

On Postgres, HNSW index builds need `maintenance_work_mem`. Queries reach the index through shared buffers and the OS page cache. Size both for the corpus.

### The SQLite event store sidecar

On DuckDB, commands append events to `<file>.events.db`, a SQLite file beside the database. The eventsourcing library owns that file. Back it up together with the `.duckdb` file; one without the other is half the corpus. A connection string that names the sidecar is refused at load. `corpus-kb doctor` probes the sidecar under "SQLite event store".

### Graph traversal: the CTE default and the DuckPGQ opt-in

The DuckDB graph store answers walks with a recursive CTE by default. It
is deterministic, needs no extension, and handles every depth the
server allows. Set `graph.duckdb_graph_backend: duckpgq` to route
traversals through the DuckPGQ community extension instead. On first
use the store installs the extension from the community repository. It
then creates a static property graph over the fixed
`entities`/`relations` tables.

DuckPGQ on duckdb 1.5.6 has known defects (each one probed live):

- Bound parameters do not reach the `GRAPH_TABLE` matcher; values travel
  as quoted literals.
- Undirected patterns (`-[e]-`) crash the binder.
- A `UNION ALL` of two `GRAPH_TABLE` arms aborts the connection.

The store works around the parameter defect with quoted literals. It
renders one fixed statement per orientation chain (depths 1 to 3).
The defects set two hard limits. Deep walks past 3 hops are not
expressible. Any install, load, or engine failure ends DuckPGQ for
that connection. The store logs one warning and stays on the recursive
CTE path permanently. Answers never change with the backend; only the
plan does.

**Which one to pick.** Keep `cte` (the default) unless you specifically
want SQL/GRAPH_TABLE plans for shallow walks. Choose `duckpgq` to
exercise the SQL/PGQ standard path on a small corpus, or to benchmark
the extension. Keep the 3-hop ceiling and the CTE fallback in mind.

---

## Module map

The storage seam lives in `src/corpus_kb/storage/`. The modules most people touch:

| Module | Role |
|---|---|
| `gateway.py` | The `SqlGateway` seam: `PostgresGateway` thin wrapper, `DuckDbGateway` single-writer file driver |
| `graph_store.py` | Abstract `GraphStore` contract (all async methods) |
| `graph_factory.py` | Builds the graph store from `graph.backend` config |
| `age_graph_store.py` | Apache AGE openCypher graph store |
| `duckdb_gateway.py` | DuckDB file-driver gateway |
| `duckpgq.py` | DuckPGQ graph traversal (opt-in behind `graph.duckdb_graph_backend`) |
| `duckdb_rag.py` | DuckDB `RagBackend` arm over the `SqlGateway` |
| `rag_backend.py` | The `RagBackend` Protocol (LlamaIndex and DuckDB arms implement it) |
| `llamaindex_backend.py` | LlamaIndex-backed RAG storage (PGVectorStore + Ollama) |
| `tenant_conn.py` | Shared tenant-scoped connection helper (Postgres RLS; DuckDB scope) |
| `dialect.py` | Storage-dialect SQL shims for the portability program |
| `search_backend.py`, `duckdb_search.py`, `duckdb_fts.py`, `duckdb_similar.py` | Search arms: shared contract, DuckDB lexical/FTS/similarity |
| `schema.sql` | DuckDB base schema (mirrors the Postgres migration set) |

## Keep the two schemas in step

Every schema change ships twice:

- the Postgres migration in `src/corpus_kb/migrations/`
- the DuckDB mirror in `src/corpus_kb/migrations_duckdb/`, same number up to 029

From 030 on, every DuckDB file runs one number above its Postgres twin. For example, pg `030_extraction_cache.sql` is duckdb `031_extraction_cache.sql`. The DuckDB-only file `030_ingest_runs_source.sql` has no Postgres twin and no rollback. It causes the shift. Applied databases record their numbers, so the numbers stay as they are. Always pair files by name, never by number. The schema-parity gate checks both name sets (`scripts/schema_parity_nameset.py`, issue #230).

Some Postgres constructs have no DuckDB shape. The mirror reshapes them:

| Postgres | DuckDB mirror |
|---|---|
| `SERIAL` / `IDENTITY` key | `SEQUENCE` default `gen_random_uuid()` |
| `halfvec(2560)` | `FLOAT[2560]` |
| `JSONB` | `JSON` |
| `UUID` | `VARCHAR` |
| `FORCE ROW LEVEL SECURITY` and policies | Left out; RLS is Postgres-only |

The schema-parity gate (Wave G) parses both migration sets and diffs the column maps. Drift fails CI on every push. The frozen tenancy manifest lists the exempt legacy tables, so the gate and `migrate-data` agree on scope.

---

## Declared differences between the engines

Same UX everywhere. Where the engines physically differ, the difference is documented and pinned by tests. The full decision log lives in [the storage-portability program, section 16](research/storage-portability-program.md). The top entries:

| Difference | Postgres | DuckDB |
|---|---|---|
| Vector search | pgvector HNSW approximate index | Exact cosine scan; optional experimental `vss` index |
| Full-text search | `tsvector` + GIN, current at read | `fts` extension, debounced rebuild; recent chunks can lag |
| Zero-vector sentinel | Zero norms order last | A `-2.0` similarity sentinel in deterministic id order |
| Numeric copy precision | `numeric` | `migrate-data` copies it as `DOUBLE` |
| Append-only tables | Database triggers | Gateway-level checks; DuckDB has no triggers |
| Event store | `eventsourcing.postgres` tables | `eventsourcing.sqlite` sidecar file |

Search responses carry honesty keys, so a client can see the mode in use: `vector_index_mode`, `lexical_index_mode`, and `embedding_mode`. Ingest-time read windows on DuckDB are listed in [ADMIN.md, "DuckDB engine notes"](ADMIN.md).

---

## Move a corpus with `migrate-data`

The command copies a whole corpus between the engines. It is CLI-only by design: the DuckDB file allows one writer, so the swap runs while the stack is down.

```bash
# Postgres to DuckDB
corpus-kb migrate-data --to duckdb:///C:/data/kb.duckdb

# DuckDB back to Postgres
corpus-kb migrate-data --to postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb \
    --from duckdb:///C:/data/kb.duckdb
```

`--from` defaults to the configured database. The command prints a JSON report: rows per table, elapsed time, the digest, and any skipped tables.

**What it copies.** Every table in foreign-key order, in both directions. The event store moves in both directions. It lands in the SQLite sidecar on the way to DuckDB. It returns to the Postgres event tables on the way back. Postgres to DuckDB reads through the DuckDB `postgres` extension. DuckDB to Postgres streams rows with psycopg.

**How it verifies.** The command compares per-table row counts, then hashes canonical rows plus events into one SHA-256 digest. A mismatch raises `ConflictError` before any success is reported: "The copied data did not match the source. Fix the cause, then run migrate-data again."

**BYPASSRLS.** Postgres forces row-level security on every tenanted table, so a plain role reads zero rows. The command checks the role first and refuses the silent-copy shape. Use a role with `BYPASSRLS`, or a superuser.

---

## Backup and restore

- Postgres: `pg_dump` and `pg_restore`. See [ADMIN.md](ADMIN.md), "Backup and restore".
- DuckDB: stop the server, then copy the `.duckdb` file and the `.events.db` sidecar. A clean shutdown has already folded the WAL in.
