# W0 Spike Findings — Migration and Event Store (agent S4)

Worktree: `.worktrees/sp-spikes-mig` (branch `feature/sp-spikes-mig`).
Probes live in `findings/probes/`; raw evidence in `findings/probes/scratch/`
(`sqlglot_report.json`, `attach_report.json`). Live-PG probes were strictly
read-only. Dates: 2026-10-19.

---

## Spike 1 — Per-table tenancy audit of forward migrations

**VERDICT: CLEAN. The manifest matches the plan's expected exemptions exactly.
No table beyond the expected list lacks `tenant_id`.**

### EVIDENCE

- Parsed all 26 forward migrations (`0*.sql`; rollbacks excluded) with a
  balanced-paren `CREATE TABLE` extractor (`findings/probes/tenancy_audit.py`).
- 40 tables created across migrations 001–026:
  - **37 tables carry `tenant_id` directly.** The "coding-domain pattern" is a
    convention, not a `DOMAIN`/type: plain `tenant_id uuid NOT NULL` in the
    column list (often part of the composite PK), an `idx_*_tenant` index,
    `ENABLE ROW LEVEL SECURITY`, and a `*_tenant_isolation` policy keyed on
    `NULLIF(current_setting('app.current_tenant_id', true), '')::UUID`.
    Files 012 (12 tables), 016 (11), 017 (3), 018 (1), 019 (4), 020 (1),
    021 (2), 022 (1), 026 (2).
  - **3 tables without `tenant_id`** — all in 001, all expected legacy
    exemptions: `corpus.sources`, `corpus.nodes`, `corpus.index_generations`.
- Out-of-`0*`-scope tables needed for the frozen manifest:
  - `storage/schema.sql` creates 12 tables (`tenants`, `documents`, `chunks`,
    `chunks_vectors`, `entities`, `relations`, `projection_checkpoints`,
    `projection_dlq`, `idempotency_keys`, `tags`, `document_tags`,
    `metadata`) — **all 12 carry `tenant_id`**.
  - `corpus.schema_migrations` is created in code
    (`src/corpus_kb/_setup/migrate.py`), no tenant column — expected global
    exemption.
  - Eventsourcing library tables, live-confirmed on the server:
    `public.corpusapplication_events`, `public.corpusapplication_snapshots`
    (lib-managed, `CREATE_TABLE=y`, `POSTGRES_SCHEMA=public`).
  - `fts_watermark` **does not exist in PG today** — it is a planned
    DuckDB-side table (`DuckDbFtsIndexManager` meta table, see
    `docs/research/storage-portability-program.md` §3.1 + FTS row). The
    manifest entry is forward-looking, not a PG audit item.

### DRAFT TENANT-EXEMPTION MANIFEST (rule 18)

| Scope | Tables |
|---|---|
| global | `tenants` (schema.sql; its PK column is itself `tenant_id` — harmless), `corpus.schema_migrations`, `fts_watermark` (DuckDB-only, future) |
| library-managed | `corpusapplication_events`, `corpusapplication_snapshots` (PG naming: `<AppName>_<events\|snapshots>`) |
| legacy | `corpus.sources`, `corpus.nodes`, `corpus.index_generations` |

Flags: **none.** Every other table (52 of 55 total incl. schema.sql) carries
`tenant_id`.

**RECOMMENDATION:** freeze this manifest as-is; the coverage test can derive
the tenant-scoped side mechanically from the same parser used in
`tenancy_audit.py`.

**AFFECTED-WP:** sp-duckdb-migrations, sp-migrate-data, sp-schema-parity-gate

---

## Spike 2 — eventsourcing.sqlite hermetic drill

**VERDICT: VIABLE. Save/read round-trip, WAL mode, and concurrent two-writer
access all work. No `subscribe()` — polling only, as the current PG EventReader
already does. Port ≈ 70–90 lines.**

### EVIDENCE (eventsourcing 9.5.6, shared venv; probe `findings/probes/es_sqlite_drill.py`)

- `PERSISTENCE_MODULE=eventsourcing.sqlite` + `SQLITE_DBNAME` env; `CREATE_TABLE=y`.
- Two independent `Application` instances on the same file: save (2 events) +
  `repository.get` round-trip returns version 2 state. `max_notification_id`
  and `select_notifications` work across instances.
- **Actual table: `stored_events`** with columns `originator_id TEXT`,
  `originator_version INTEGER`, `topic TEXT`, `state BLOB`
  (composite PK). **There is no `notification_id` column** — the notification
  id is SQLite `rowid` (`SELECT MAX(rowid)` / `SELECT rowid, * ... ORDER BY
  rowid` per the lib source). With snapshotting enabled the factory also
  creates `stored_snapshots` (naming is `"stored_" + purpose`).
- `PRAGMA journal_mode` → **`wal`** (lib default).
- `SQLiteApplicationRecorder.subscribe()` raises
  `NotImplementedError` ("does not support subscriptions"); the PG recorder
  gets a LISTEN/NOTIFY `PostgresSubscription`. The current PG read path
  (`src/corpus_kb/projections/event_reader.py`) is already a
  `notification_id` poller, so nothing is lost.
- Concurrency: two threads × 25 saves on one file → **50/50 succeeded, zero
  lock errors** (`SQLITE3_DEFAULT_LOCK_TIMEOUT = 5` s + WAL).
- **Name asymmetry (surprise):** PG tables are named after the application
  class (`corpusapplication_events`/`_snapshots`, live-confirmed); SQLite uses
  fixed `stored_events`/`stored_snapshots`. Any schema-parity or migrate-data
  inventory must not assume one name.

### EventReader polling port estimate

Current PG reader: 113 lines (`event_reader.py`). Port approach:

- Build `read_since` on the recorder's own
  `select_notifications(start, limit, stop, inclusive_of_start=False)`.
  `Notification` extends `StoredEvent`, so `state` is included and the
  `mapper.to_domain_event` decode path is unchanged.
- The recorder is synchronous → wrap the call in `asyncio.to_thread` (or run
  direct `sqlite3` `SELECT rowid, * FROM stored_events WHERE rowid > ?
  ORDER BY rowid LIMIT ?` in a thread — mirrors lib internals, avoids holding
  the app object).
- Reused unchanged: `DomainNotification`, `event_timestamp_dt`, topic
  suffixing, decode-by-mapper rule. New code ≈ **70–90 lines**.

**RECOMMENDATION:** port as a thin `SqliteEventReader` next to the PG one;
keep "SELECT-only, lib-owned table" discipline; assert the `stored_events`
name at startup like the PG side does via `events_table_name(app)`.

**AFFECTED-WP:** sp-es-sqlite

---

## Spike 3 — Live-PG DuckDB ATTACH probe (read-only)

**VERDICT: WORKS. ATTACH ... (TYPE POSTGRES, READ_ONLY) against the live
`corpus_kb` DB succeeds; the expected type mappings hold; CTAS copies fine
but drops every constraint.**

### EVIDENCE (DuckDB 1.4.5 + postgres scanner; probe `findings/probes/attach_probe.py`)

Reachability: `localhost:5432` up (`postgres:postgres` superuser, 3 s asyncpg
connect); live DB `corpus_kb`.

DuckDB-side types seen over the attachment:

| PG type (live) | DuckDB type |
|---|---|
| `halfvec` (chunks_vectors.embedding) | `VARCHAR` — arrives as `[v,v,...]` text; sample parsed to **2560 floats**, matching the `dimensions` column |
| `vector` (chunks_vectors.vector_1024, code_registry vectors) | `VARCHAR` (all live values NULL today, but the text shape is the same) |
| `jsonb` (documents.metadata, chunks.metadata, code_registry.examples, ...) | `VARCHAR` — **not** DuckDB `JSON`; plan must normalize or keep text |
| unbounded `numeric` (9 columns: code_registry.pool_floor/residual_floor, code_keywords.ll/log_ratio, chunk_signals.score, chunk_codes.confidence, final_codes.confidence, doc_extraction_audit.miss_rate_point/ub95) | `DOUBLE` — precision-loss point to document |
| `uuid` | `UUID` |
| `timestamptz` | `TIMESTAMP WITH TIME ZONE` |
| `varchar` / `integer` / `bigint` / `boolean` | direct |

CTAS: `CREATE TABLE scratch_vectors AS SELECT * FROM src.public.chunks_vectors
ORDER BY chunk_id LIMIT 5` (plus `chunks`) into a scratch `.duckdb` file →
rows arrive; `duckdb_constraints()` is **empty** → PK/FK/NOT NULL/defaults are
all dropped by CTAS. `halfvec` text is parseable after copy (verified on a
non-NULL row; note the first 5 rows by `chunk_id` happen to have NULL
embeddings — degraded fallback rows exist in the live DB).

**RECOMMENDATION:** migrate-data copy path = read-only ATTACH + per-table CTAS
+ (a) constraint/index rebuild pass after copy, (b) halfvec/vector text →
DuckDB-native float-list conversion step, (c) explicit decision on jsonb
text-vs-JSON. Never attach read-write in the migration path.

**AFFECTED-WP:** sp-migrate-data, sp-schema-parity-gate

---

## Spike 4 — Event-store volume

**VERDICT: 2 events. Copy-through-API is trivially justified; volume rules out
nothing and justifies no bespoke bulk path.**

### EVIDENCE

- `public.corpusapplication_events`: `count(*) = 2`, `notification_id` 1..2
  (DuckDB attach query, cross-checked via asyncpg: 2).
- `public.corpusapplication_snapshots`: 0 rows.
- Table names verified live against
  `src/corpus_kb/domain/application.py` (`CorpusApplication` →
  `<appname>_events`/`_snapshots`).

**RECOMMENDATION:** for migrate-data, replay the 2 events through the
application API (or straight `INSERT ... SELECT` over the attach); no
re-projection machinery needed. Re-measure before freezing if production
installs grow the store.

**AFFECTED-WP:** sp-migrate-data

---

## Spike 5 — sqlglot static audit (flagger only)

**VERDICT: SYNTAX-CLEAN, SEMANTICALLY NOT. 0 parse failures and 0 transpile
exceptions across all 26 forward migrations (263 statements) — but sqlglot
passes non-portable PG DDL through VERBATIM, so the W0 gate must be a
construct flagger, not an exception catcher.**

### EVIDENCE (sqlglot 30.23.0 in throwaway `.venv-sqlglot`; probe `findings/probes/sqlglot_audit.py`)

- Full per-file detail: `findings/probes/scratch/sqlglot_report.json`
  (families, parse/transpile results, regex flags per file).
- `sqlglot.parse_one(..., read='postgres')` succeeds for every statement,
  including `CREATE POLICY`, `FORCE ROW LEVEL SECURITY`, `CREATE TRIGGER`,
  `CREATE EXTENSION` — these parse as generic `Command` nodes.
- Spot-check of `transpile(write='duckdb')` output (verbatim passthrough):
  - `CREATE POLICY ... USING (tenant_id = ... current_setting(...)::UUID)` → unchanged
  - `CREATE INDEX ... USING hnsw (embedding halfvec_cosine_ops) WITH (m=16, ef_construction=64)` → unchanged
  - `embedding halfvec(2560)` → unchanged; `vector(1024)` → `VECTOR(1024)` (still pgvector, not DuckDB)
  - `ALTER TABLE t FORCE ROW LEVEL SECURITY` → unchanged
  - `CREATE TRIGGER ... EXECUTE FUNCTION f()` → **body silently dropped**
    ("Unsupported property triggerproperties")
  - `USING gin (to_tsvector('english', text))` → unchanged

Construct families (global counts over migrations):

| Family | Count | | Family | Count |
|---|---|---|---|---|
| `DO $$` plpgsql blocks | 67 | | `vector(n)` columns | 12 |
| `FORCE ROW LEVEL SECURITY` | 42 | | `gen_random_uuid()` | 8 |
| `current_setting()` | 21 | | `CREATE TRIGGER` | 8 |
| `ENABLE ROW LEVEL SECURITY` | 20 | | `USING hnsw` | 7 |
| `CREATE POLICY` | 20 | | vector opclasses (`*_cosine_ops`) | 7 |
| `SET app.*` GUC | 12 | | plpgsql `CREATE FUNCTION` | 6 |
| `halfvec(n)` columns | 5 | | `CREATE EXTENSION` | 4 |
| `USING gin` | 3 | | `CREATE SCHEMA` | 2 |
| `to_tsvector` family | 2 | | uuid-ossp / AGE `create_graph` | 1 / 1 |

003–005 are extension-gated (`vector`, `uuid-ossp`, AGE, pgml) and already
skipped with a loud warning by the shared bootstrap; their constructs need no
DuckDB migration, only the documented skip.

Method note: the statement splitter must skip `;` inside `--` comments and
`$$` blocks (first probe run mis-split on prose; fixed in
`sqlglot_audit.py`).

**RECOMMENDATION:** W0 automated audit = (1) sqlglot parse gate (expect 0
failures; a failure means real syntax drift) + (2) the FLAGS classifier from
`sqlglot_audit.py` (regex/AST) as the semantic gate, with a per-file allowlist
for the DuckDB `migrations_duckdb/` variants. Reuse it in the schema-parity
gate. Never use transpile output as SQL.

**AFFECTED-WP:** sp-schema-parity-gate, sp-duckdb-migrations

---

## Surprises (cross-spike)

1. **Events-table naming asymmetry:** SQLite `stored_events`/`stored_snapshots`
   (fixed) vs PG `corpusapplication_events`/`_snapshots` (application-class
   derived); SQLite has **no `notification_id` column** — rowid plays that
   role.
2. **sqlglot is silently permissive:** exception-based gates would report
   "everything portable" while the emitted DuckDB DDL is unexecutable
   (`CREATE POLICY`, `USING hnsw`, `halfvec` passthrough; `CREATE TRIGGER`
   body dropped).
3. `fts_watermark` is a **future DuckDB-only** table — the manifest entry is
   forward-looking; nothing to audit in PG.
4. Live `chunks_vectors.vector_1024` is entirely NULL and the lowest-`chunk_id`
   rows have NULL `embedding` (degraded-mode rows) — the migration copy step
   must tolerate NULL vectors and the parity gate should expect them.
5. DuckDB maps unbounded `numeric` → `DOUBLE` (silent precision policy) and
   `jsonb` → plain `VARCHAR` (no JSON typing) across the postgres scanner.
