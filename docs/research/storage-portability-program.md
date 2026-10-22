# Storage Portability Program — Postgres ⇄ DuckDB

- **Status:** EXECUTED (2026-10-20). All eight phases merged and CI-green: #138 (phase 0), #140 (A), #141 (B), #144 (C), #145 (D), #146 (E), #147 (F), and this PR (G, closeout).

  PR ledger: Phase 0 (W0 spikes) #138 · Phase A (config truth, extra, graph catalogue prep, query fusion) #140 · Phase B (gateway, dialect, DuckDB migrations) #141 · Phase C (graph ops, search seam, DuckDB store) #144 · Phase D (core ports) #145 · Phase E (domain ports) #146 · Phase F (setup/doctor UX, full-parity harness, hardening) #147 · Phase G (schema-parity gate, benchmark, docs) this PR.
- **Goal:** A Corpus-KB end user switches the storage engine between the Postgres ecosystem and the DuckDB ecosystem with a config change and one data-migration command. **V1 scope is full feature parity across all UX areas**: every user-facing operation behaves identically on both engines. The graph model (documents → chunks → entities → relations) stays the canonical data map. Neighbor traversal at 1–3 hops is first-class on every surface, with a clean extension path past 3 hops.
- **Binding rules:** This program follows the zero-drift program ([harness-zero-drift-program.md](harness-zero-drift-program.md)). No rule is routed around.
- **Program goals (user-set):** (1) every CI pass green on first attempt, (2) the end state is more elegant and easier to maintain than today, (3) implementation uses the maximum number of effectively parallel subagents.

---

## 0. Session goals & stress-test verdicts (v3, 2026-10-15)

The goal ledger distilled from this session, each stress-tested against the plan as written. Grounding sources in §18.

| # | Goal (from the session) | Verdict | Where it holds | Changed this round |
|---|---|---|---|---|
| G1 | End users easily swap between the Postgres and DuckDB ecosystems | **PASS — product-UX gaps closed** | §3.3, §9, §1.2 | `setup --backend duckdb` one-command path (installs the extra itself); DuckDB-first quickstart in docs; documented engine-selection policy; version pin `duckdb>=1.4,<2` (LTS) |
| G2 | Graph first-class: 1–3 hops first-class, >3 easy, graph-shaped data mapping | **PASS, enhanced** | §4, §5 | `WITH RECURSIVE … USING KEY` noted as an optional DuckDB-side accelerator; the portable classic CTE stays the contract |
| G3 | V1 = full feature parity across all UX areas | **PASS by construction** | §1 matrix, §12 harness | `sql_query` added to the honesty matrix — user-supplied SQL is dialect-exposed by nature |
| G4 | Every CI pass green on first attempt | **PASS — 24 hard rules** | §11 | — |
| G5 | End solution more elegant and easier to maintain | **PASS — externally validated** | §3, §17 | The gateway matches the industry pattern for multi-backend apps (Grafana's thin store layer + config-selected dialect); ORM abstraction rejected on the evidence |
| G6 | Max effective parallel subagents | **PASS — 27 WPs, 8 phases** | §13 | — |
| G7 | Decisions grounded in authoritative sources; searches resolve ambiguity | **PASS** | §18 | +8 sources this round |
| G8 | OSS tools where they make it easier and more reliable | **PASS** | §17 | pg_duckdb, DuckLake, MotherDuck evaluated and rejected with reasons |

**The honest tension this stress test surfaces:** the industry's most-cited lesson is Mattermost dropping MySQL — *unmanaged* multi-backend support decays and gets retrenched. G3 (full parity) walks straight at that. The plan's answer is the same thing that separates Grafana (multi-backend sustained for a decade) from Mattermost (retreat): **parity is CI-enforced forever** — the full-parity harness and schema-parity gate run on every push, so both engines stay honest structurally, not by discipline. If the cost ever proves unsustainable, the documented fallback is a deliberate narrowing, never silent drift.

### 0.1 Repo delta absorbed into this revision (2026-10-17)

The plan was cut against master @ 6fe7221; 22 commits (config-alignment #101–#114, UX program #115–#124) moved master to **fa02dd7** while the plan was in review. Facts re-based:

- **Parity mandate widened (#110):** every catalogue op (~35 today) must declare **all four** surfaces — MCP, CLI, HTTP, socket (`test_catalogue_and_legs_agree` asserts it). The new graph ops gain CLI commands; §5.1/§11 rule 3 updated.
- **Dimension regime (#101):** `halfvec(2560)` is shipped (migrations 025/026; `embedding.dimensions` default 2560; Postgres *requires* halfvec there — pgvector HNSW caps plain vector at 2000). DuckDB maps it to `FLOAT[2560]`; sizing re-derived (§10); matryoshka now `retrieval.matryoshka_enabled` (default off, requires `mrl_trained`).
- **Config namespace v2 (#101):** new keys live under `retrieval.*` (`search.*` spellings are normalized aliases) — the plan's two new keys renamed (§11 rule 2).
- **Reranking (#113):** a real cross-encoder backend shipped — `reranking:` block, backend `llamacpp` via `/v1/rerank`, model `qwen3-reranker:4b`. Engine matrix updated.
- **New surfaces the plan inherits (#116–#121):** the `ask` op (`tools/ask_tools.py`, `catalogue_ask`, parity leg exists), the OpenAI-compatible `/v1` adapter (`api/routes_v1.py`), the NiceGUI web UI (behind the `ui` extra — consumes surfaces, stays storage-blind), OpenTelemetry tracing with a `trace_id` envelope field. All ride the handler seam; the `SearchBackend` serves `query`, `ask`, and `/v1` alike (§13).
- **Unchanged facts verified:** ghost `search_graph`/`get_entity_relations` names are still unregistered (`surface_registry.py:613-614`); `tests/research_db.py` still hardcodes its DSN (rule 20 stands); no `duckdb` dependency anywhere yet; no `migrations_duckdb/` yet; `corpus-kb search` CLI (#107) is the CLI-surface precedent the graph ops follow.

## 1. Scope: full feature parity

V1 DuckDB = the same product. Every UX area works on both backends with identical envelopes:

| UX area | Operations | Parity in v1 |
|---|---|---|
| Ingest | `ingest_file`, `ingest_text`, `ingest_url`, strategies, hierarchy, `ingest-status`, `ingest-reembed`, watch | Yes |
| Search/query | `search`, `query`, `list_documents`, `verify_answer`, filters, degraded handling | Yes |
| Graph | `add_entity`, `add_relation`, `search_graph`, `get_entity_relations`, `graph_neighbors` (hops 1–3), `graph_traverse` | Yes |
| Tags / versioning | tag ops, version ops | Yes |
| Research domain | projects, units, exchanges, retrieval, coding assist, reports, releases | Yes |
| Coding domain | 12-table qualitative coding stack | Yes |
| Admin | `setup`, `doctor`, `migrate-data`, config truth | Yes |

### 1.1 Engine-difference honesty matrix

Same UX everywhere. Where the engines physically differ, the difference is a documented note, never a missing feature and never a silent downgrade (the `embedding_mode`/`embedding_note` contract shape):

| Concern | Postgres engine | DuckDB engine | User-visible difference |
|---|---|---|---|
| Concurrency | Multi-process, RLS-isolated | Single-writer file lock; one process opens read-write | Deployment note in doctor + docs |
| Tenancy | RLS **plus** explicit `tenant_id` predicates | Explicit `tenant_id` predicates | None |
| Embeddings | pgml (in-DB) or Ollama | Ollama (pgml refused at config load) | `embedding_note` |
| NER extraction | regex / langextract / pgml / gliner / bert | regex / langextract / gliner / bert | Config validation message |
| Vector search | pgvector HNSW, iterative scan, halfvec | Exact scan (`array_cosine_distance`), optional `vss` HNSW (core extension, still experimental) | `vector_index_mode` + note |
| Full-text search | `tsvector` + GIN | `fts` core extension, managed rebuild | `lexical_index_mode` + note |
| Raw SQL passthrough | `sql_query` executes user SQL on Postgres | Same op executes on DuckDB | Dialect note: user SQL may behave differently per engine; single-tenant posture (§3.1) |
| Graph engine | AGE openCypher or CTE (config) | CTE (recursive + fixed joins) | None — same operations |
| Matryoshka probe | `subvector()` | ARRAY slice `embedding[1:n]` | None |
| Reranking | pgml.rank or cross-encoder | Configured non-pgml reranker — `reranking:` block, backend `llamacpp` (`/v1/rerank`) | Config validation message |
| Append-only tables | BEFORE UPDATE/DELETE triggers | Gateway-level enforcement (DuckDB has no triggers) | None |
| Event store | `eventsourcing.postgres` | `eventsourcing.sqlite` (file beside the `.duckdb`) | None |

### 1.2 Engine selection guidance (who runs what)

Grounded in DuckDB's own positioning (in-process, single-writer, analytical; "a poor fit for multi-client concurrent-write app backends" per its concurrency docs and ecosystem write-ups) and the Grafana precedent (embedded default for single-node, server database at scale):

- **DuckDB is the right engine when** one person runs one server process against a local corpus — the README's "No cloud" use case. Zero Docker, one file to back up (plus the SQLite event store), fast exact-scan search at embedded scale. Working ceiling: ≤ ~100k chunks at the shipped 2560 dims, pending the benchmark gate (§10).
- **Postgres is the right engine when** multiple people or processes share a corpus, deployments need HA, or research/coding analytics run at scale (pgvector HNSW, halfvec, in-database pgml).
- **The default stays `postgres`.** The user goal is an easy *swap*, not a default flip; existing installs and the research stack keep their behavior. DuckDB is the documented light-local path.
- `corpus-kb doctor` prints the engine in use and, when the corpus pattern matches the other engine's sweet spot, a one-line suggestion — never a warning. Both engines are fully supported.

## 2. Current-state facts (survey + pre-mortem evidence)

- The graph seam is clean: `GraphStore` ABC (`src/corpus_kb/storage/graph_store.py:31`), one selection point (`create_graph_store`, `server_wiring.py:39-67`). AGE was added before with zero shared infrastructure — the precedent.
- The query/RAG side takes raw `asyncpg.Pool` everywhere: `QueryHandler`, `CommandHandler`, `TagHandler`, `IdempotencyChecker`, `VersioningHandler`, `RouterHandler`, `CodingHandler`, `CheckpointManager`, `DLQHandler`, `EventReader`, projections, `LlamaIndexPostgresBackend` (hard-instantiated, `server_wiring.py:223-226`), plus `tools/ingest_store.py`, `research/*`, `projections/research/*`, `query_crud.py`, `parent_expand.py`. ~30 modules, one mechanical pattern.
- Traversal today: `bfs()` via `WITH RECURSIVE` (`storage/graph_store.py:372-412`); HTTP-only routes `/api/graph/search|bfs|relations/{id}`; **no MCP tool, no catalogue row, no parity leg**; ghost names `search_graph`/`get_entity_relations` in `SERVER_AUTO_APPROVE` (`surface_registry.py:549-550`) with no registered tool. Relations rows have no ORDER BY; `/api/graph/relations/{id}` hardcodes the default tenant (`routes_graph.py:112`).
- Fusion runs in Postgres: `corpus.rrf_fusion` (migrations 006/024), called by `handlers/query_handler.py:322-329` and `research/retrieval.py:503-510`. Determinism pinned `score DESC, chunk_id ASC` by `tests/test_query_determinism.py`, which **file-scans** specific modules (exact-count DESC-clause audit) and pins the fusion SQL in place (`query_handler.py:267-271`).
- Eventsourcing: `domain/application.py:25-44` builds `PERSISTENCE_MODULE=eventsourcing.postgres` + `POSTGRES_*` env. The library ships `eventsourcing.sqlite` (`SQLITE_DBNAME` etc.), same selection mechanism.
- OllamaEmbedder cache is in-process (`rag/embedder.py:98`) — nothing to migrate.
- Repo refresh (2026-10-17, master fa02dd7): ~35 catalogue ops across `catalogue*.py` (ask, topics, research, release added); parity deep-test asserts **all four surfaces per op**; `embedding.dimensions` defaults **2560** with `vector_type: halfvec` (PG requires halfvec — HNSW caps plain vector at 2000; migrations 025/026); new config namespace `retrieval.*` (`search.*` alias-normalized); `reranking:` block with llamacpp backend; extras grew (`ui`, `eval`, `bertopic`, …); `trace_id` envelope field via otel (#121). Detail in §0.1.
- Verified DuckDB facts (refreshed 2026-10-15 against the 1.4 LTS line): **`duckdb>=1.4,<2` is the pin** — 1.4.0 LTS (Sept 2025, one-year support; database encryption, `MERGE`); `fts` and `vss` are **core extensions** there (vss still experimental: in-memory HNSW default, persistence crash-unsafe, FLOAT32 only, no filtered scans); FTS still has **no auto-refresh** (index rebuilt on change); `duckpgq` SQL/PGQ remains a community extension (deliberately unused — recursive CTEs + fixed joins cover 1–N hops); `WITH RECURSIVE … USING KEY` (May 2025) dedups frontier keys per iteration and recursive-CTE execution got faster in 2026 — an optional accelerator, not the portable contract (§5.2); Python client is **synchronous**, single-writer per file; `ON CONFLICT`, `RETURNING`, `DISTINCT ON`, `FILTER (WHERE)`, recursive CTEs, `ILIKE`, `gen_random_uuid()` all supported (W0 spike confirms per-family).

## 3. Target architecture

One config key selects the ecosystem:

```yaml
database:
  backend: postgres        # postgres | duckdb
  connection_string: postgresql://...   # or duckdb:///C:/data/corpus.duckdb
```

- New env var `CORPUS_KB_DATABASE_BACKEND` → `database.backend` (env map + `KNOWN_CORPUS_KB_ENV_VARS` + `docs/ADMIN.md`, all three or doctor flags drift).
- `graph.backend` (`age`|`postgres`) only applies when `database.backend: postgres`; the mix is rejected by a pure function in `config.py` (the `matryoshka_mrl_error` precedent) surfaced by `load_config`, `validate_configs`, and `doctor`.
- PG-only engines (pgml embedder/reranker/NER, AGE) are refused at config load on `duckdb` with a plain-English remediation; the client-side equivalents in §1.1 serve the same UX.

### 3.1 `storage/gateway.py` — the one seam

Full parity makes ~30 modules storage clients. One protocol, two drivers:

```python
class SqlGateway(Protocol):
    async def fetch(self, sql: str, *args) -> list[Row]: ...
    async def fetchrow(self, sql: str, *args) -> Row | None: ...
    async def fetchval(self, sql: str, *args) -> Any: ...   # 25+ call sites
    async def execute(self, sql: str, *args) -> str: ...    # PG-format status tag
    def transaction(self, tenant_id: str) -> AbstractContextManager["SqlGateway"]: ...
```

- `Row = asyncpg.Record | dict[str, Any]` — **zero adapter on Postgres**: `asyncpg.Record` already supports key and positional access and every consumer does `row["col"]` / `dict(row)` / `row[0]`. Only `DuckDbGateway` builds dicts (cursor description), supporting both access styles.
- `execute()` returns PG-format status tags synthesized from cursor rowcount on DuckDB (`"UPDATE 3"`, `"INSERT 0 1"`) — `projections/research/_common.py` idempotency counting parses these strings verbatim.
- **Nested `transaction()` is a depth-counter no-op on DuckDB, not an error.** DuckDB has no SAVEPOINT but also no aborted-transaction state — a failed statement leaves the transaction usable, so the fail-open degraded-probe and vector-arm-isolation contracts (pinned by `tests/test_vector_arm_isolation.py`) port unchanged. W0 spike confirms the statement-failure semantics.
- `PostgresGateway(pool)`: wraps asyncpg; `transaction(tenant_id)` keeps `set_config('app.current_tenant_id', ...)` so RLS stays defense-in-depth. When the last consumer ports, `storage/tenant_conn.py` and the inline `set_config` call sites retire (a repo test asserts zero `set_config(` outside the gateway).
- `DuckDbGateway(conn)`: one `DuckDBPyConnection`; an `asyncio.Lock` held across a whole transaction scope (concurrent transaction scopes must not interleave on one connection) plus a `threading.Lock` guarding individual sync calls under `asyncio.to_thread`; reads serialize behind the write path (documented posture, §1.1). Append-only table list enforced in `execute()`.
- **Held-connection escape hatch** `advisory_lock_ctx()`: `tools/ingest_common.py` checks out one pooled connection and holds it across ingest for the advisory lock — the hold must survive, not just the SQL. PG maps it to the held-conn `pg_try_advisory_lock` pair; DuckDB maps it to the inherent single-writer lock (no-op).
- **Graph stores stay off the gateway.** `PostgresGraphStore`/`AgeGraphStore` need session-scoped GUC semantics (`graph_store.py:159-182`); `create_graph_store` keeps taking `pool`/`conn`, and `GraphStore` remains the second, pre-existing seam. Forcing session-GUC into SqlGateway for one consumer would be the over-engineering this program cut.
- **Explicit-tenancy rule with a manifest:** every predicate-able SQL string carries an explicit `tenant_id` predicate on both backends (write paths already do; RLS-only statements concentrate in `idempotency.py` and `versioning_handler.py`). A frozen **tenant-exemption manifest** — global: `tenants`, `schema_migrations`, `fts_watermark`; library-managed: eventsourcing tables; legacy: `corpus.sources/nodes/index_generations` — drives a coverage test, `migrate-data`'s table inventory, and the schema-parity gate. Three surfaces are unpredicateable and documented: `sql_query` (user-supplied SQL — cross-tenant on DuckDB; allowlist or single-tenant posture note), eventsourcing table reads, and `pg_tables` metadata.
- Adoption is mechanical: constructor takes `SqlGateway` instead of `asyncpg.Pool`; SQL audited against the portable subset; non-portable fragments route through the dialect (§3.2). Determinism-pinned files keep byte-identical PG output.

### 3.2 `storage/dialect.py` — shims, not a dialect layer

A small frozen object injected at construction. PG outputs are byte-identical to today's strings:

| Shim | Postgres | DuckDB |
|---|---|---|
| `vector_distance(col, param)` | `<=>` cast forms | `array_cosine_distance(col, param::FLOAT[k])` |
| `vector_cast(idx, dims)` | `::vector(n)` / `::halfvec(n)` | `::FLOAT[n]` |
| `matryoshka_probe(col, dims)` | `subvector/l2_normalize` | `col[1:dims]` slice |
| `fts_match(col, param)` / `fts_rank(...)` | `@@ plainto_tsquery` / `ts_rank(to_tsvector(...))` | `fts_main_chunks.match_bm25(...)` score |
| `advisory_lock(key)` / `advisory_unlock` | `pg_try_advisory_lock` / unlock | no-op (single-writer lock already serializes) |
| `hnsw_settings(...)` | `SET LOCAL hnsw.*` (version-gated) | no-op |
| `uuid_new()` | `gen_random_uuid()` | `gen_random_uuid()` |
| `planner_flags(...)` | `SET LOCAL enable_seqscan/sort` | no-op |

Crossover rule: a fragment shared by ≥2 modules and expressible in place → shim; a whole-statement or output-shape divergence → per-module twin. Expected twins: `to_regclass()` (`ingest_watch.py`), `pg_tables`/`tableowner` reads (`versioning_handler.py` — the versioning envelope gains an honesty note), `EXPLAIN (FORMAT TEXT)` output shape (`analytics_sql.py`).

Portable SQL stays literal in modules. Anything not in the table is audited in W0; a query that cannot shim gets a per-module twin (two constants, one function selecting them) — still no abstraction layer.

### 3.3 Backend classes and components

| Concern | Postgres | DuckDB |
|---|---|---|
| Graph store | `PostgresGraphStore` / `AgeGraphStore` | `DuckDbGraphStore` (same ABC; tenant WHERE; owns `transaction()`; error translation at its boundary, the `_age_unavailable` precedent). Off the gateway — session-GUC semantics (§3.1) |
| Search orchestration | `PostgresSearchBackend` — current `_search_in_connection` arms moved **byte-identical** out of `query_handler.py` | `DuckDbSearchBackend` — exact-scan cosine arm, `match_bm25` arm, degraded probe, `vector_index_mode`/`lexical_index_mode` notes |
| FTS index | tsvector GIN (auto) | `DuckDbFtsIndexManager`: create at migration, watermark, debounced rebuild at ingest-commit boundaries, `fts_watermark` meta table |
| Shared traversal SQL | `storage/graph_sql.py`: pure builders `neighbors_sql(hops, direction)` + `traverse_sql(max_depth)`, imported by PG + DuckDB stores (AGE keeps cypher) |
| Fusion | SQL function (research path only) | `handlers/query_fusion.py`: pure `rrf_fuse()` implementing migration-024 total order; handler re-asserts `(-score, chunk_id)` |
| Schema | `migrations/*.sql` + asyncpg runner | `migrations_duckdb/*.sql` mirroring ALL tables (core + research + coding) + backend-dispatched runner in `_setup/migrate.py`, own `schema_migrations` |
| Event store | `eventsourcing.postgres` | `eventsourcing.sqlite`; `_eventsourcing_env(backend, conn_str)` 5-line builder; `get_app` keyed on `(backend, conn_str)` |
| RAG hook | `LlamaIndexPostgresBackend` | `DuckDbRagBackend` (~80 lines) over `chunks_vectors`, same `RagBackend` protocol |
| Data swap | `handlers/migrate_data.py` — **CLI-only** (the server holds the DuckDB file lock; HTTP/socket surfaces would be a hazard). PG→DuckDB reads the source through the DuckDB **postgres extension** (`ATTACH … (TYPE POSTGRES)`, §17) with the Python stream loop as fallback; DuckDB→PG uses the Python stream loop. Digest verified both directions, plus an explicit event-store leg (§4) |

**`SearchBackend` protocol** (`storage/search_backend.py`, one method — `search(SearchRequest) -> SearchOutcome`): lives in `storage/` so implementations import downward (layering). `QueryHandler(search_backend=None)` defaults to the PG impl built from its own gateway — every existing constructor and test keeps working.

### 3.4 New-file list (all under the 250-line limit)

| File | ~Lines | Responsibility |
|---|---|---|
| `storage/gateway.py` | 200 | Protocol + both drivers + Row adapter + append-only enforcement |
| `storage/dialect.py` | 120 | Shim table (PG byte-identical outputs) |
| `storage/search_backend.py` | 60 | Protocol + `SearchRequest`/`SearchOutcome` dataclasses |
| `storage/duckdb_store.py` | 250 | `DuckDbGraphStore` |
| `storage/duckdb_search.py` | 250 | `DuckDbSearchBackend` (+ `duckdb_fts.py` if split needed) |
| `storage/graph_sql.py` | 120 | Neighbor/traverse SQL builders (shared) |
| `handlers/query_search_pg.py` | 250 | PG search backend (pure move) |
| `handlers/query_fusion.py` | 80 | `rrf_fuse` + provenance re-attach |
| `handlers/migrate_data.py` | 200 | Stream copy + digest + report |
| `storage/duckdb_rag.py` | 80 | `RagBackend` over `chunks_vectors` |
| `migrations_duckdb/001…NNN.sql` | — | Full schema mirror |
| `duckdb_path helper` (in gateway) | 20 | `duckdb:///` ⇄ OS path, Windows drive letters |

`server_wiring.py` diff shape (~15 lines): read `backend` once → `pool` only when postgres → `gateway = create_gateway(cfg, pool, backend)` → `graph_store = await create_graph_store(cfg, gateway, backend)` (one new branch) → `search_backend = create_search_backend(...)` → `app = get_app(conn_str, backend=backend)`. No façade object; two sibling factories beside each other, matching the flat composition style.

## 4. Data model mapping

`migrations_duckdb/` mirrors every table 1:1 — core (documents, chunks, chunks_vectors, entities, relations with migration-007/008/009/022 provenance), research domain (migration 016+), coding domain (012–015), releases (019), caches (020), shadow (021), integrity (022), qa_context (023), halfvec regime (025), topics (026). Type translations are mechanical: `JSONB`→`JSON` (key-order fidelity is a W0 spike), **`halfvec(2560)`→`FLOAT[2560]`** (the shipped PG regime — halfvec is required there because pgvector HNSW caps plain vector at 2000), `UUID`→`VARCHAR` (format unchanged; envelopes byte-stable), triggers → gateway enforcement + migration-012 trigger bodies documented as comments. Because the logical map is identical, swap = row copy with digest verification. `migrate_data`'s inventory = the mirrored tables **plus the tenant-exemption manifest (§3.1) with per-table disposition** (copy / skip / special — `corpus.schema_migrations`, eventsourcing library tables, `tenants`, and legacy `corpus.*` are outside the mirror); it verifies per-table counts plus a SHA-256 corpus digest over ordered `(table, pk)` rows on both sides. **The event store is an explicit leg:** events live in lib-owned Postgres tables (`eventsourcing.postgres`) and must reach the SQLite file via a W0-decided mechanism (copy through the eventsourcing store API, or re-project from snapshots) — a table-only copy would silently drop all command history.

## 5. First-class hop traversal

### 5.1 Registry-first surface additions

New catalogue rows (`contracts/catalogue_documents.py`) + parity `LEGS` entries + shared-leg functions + **all four surface wirings (MCP, CLI, HTTP, socket — parity asserts all four per op since #110)** land in **one commit per op** (the parity test asserts `set(LEGS) == {op names}`; anything less reds all three OSes):

| Op | Params | Envelope (ordered) | Surfaces |
|---|---|---|---|
| `search_graph` | query, entity_type?, limit | rows ordered `name, entity_id` | HTTP + socket + MCP + CLI |
| `get_entity_relations` | entity_id, limit | rows ordered `relation_id` | HTTP + socket + MCP + CLI |
| `graph_neighbors` | entity_id, **hops: 1–3** (default 1), direction `in|out|both`, relation_types?, entity_types?, limit | rows ordered `depth, name, entity_id` | HTTP + socket + MCP + CLI |
| `graph_traverse` | entity_id, max_depth (2–`graph.max_hops`), same filters | same shape + `truncated: bool` | HTTP + socket + MCP + CLI |

Registry governance: **do not touch `surface_registry.SURFACES`** (every non-catalogue `mcp_tool` there needs a `REGISTRY_TOOL_FUNCTIONS` entry or MCP build fails at import). **Keep** the ghost `SERVER_AUTO_APPROVE` entries — they become real when the catalogue tools land; removing them would churn four editor configs and the generated codex.json for nothing. Tools exposed to editors get `VALID_TOOL_NAMES` entries in the same commit, and hand-maintained mcp-configs updated identically (`poe gen` at integration).

The v1 accidents fixed alongside: relations rows get a total order; the tenant hardcode in `routes_graph.py:112` becomes a parameter; `/api/graph/bfs` is deprecated in favor of `graph_traverse` (route kept one release, documented).

### 5.2 Fast path and extension path

One handler (`handlers/graph_handler.py`), two store methods on the `GraphStore` ABC — **ABC change + all three implementations + handler land in one commit** (an abstract method without all implementations explodes at instantiation across the suite):

- `neighbors(entity_id, hops, direction, relation_types, entity_types, limit)` — hops ≤ 3: fixed chain of self-joins over `relations` from `graph_sql.neighbors_sql(hops, direction)` (hop-N = N joins; generated by a loop). Index-friendly, identical shape on PG and DuckDB. First-class = the common case is the fast, simple case.
- `traverse(entity_id, max_depth, …)` — `WITH RECURSIVE` with path-array cycle guard, ordered `(depth, name, entity_id)`. Works on both engines unchanged. `graph.max_hops` (default 5, ceiling 25 = today's AGE cap) is config, so **going past 3 hops needs no engine code**; adding a hop-4 fast path later is a constant change in the builder loop. Optional later accelerator on DuckDB only: `WITH RECURSIVE … USING KEY` dedups frontier keys per iteration — exactly BFS min-depth semantics — and DuckDB shipped dedicated recursive-CTE execution improvements in 2026; the classic portable CTE remains the contract so both engines share one builder.

## 6. Hybrid search parity

`QueryHandler` keeps owning orchestration (one op, one handler). The `SearchBackend` boundary carries the engine split:

- **Lexical (DuckDB):** `PRAGMA create_fts_index('main.chunks', 'chunk_id', 'text', stemmer='porter', stopwords='english')`; query `fts_main_chunks.match_bm25(chunk_id, $q)`. No auto-refresh upstream → watermark + debounced rebuild at ingest-commit boundaries; envelope keys `lexical_index_mode: "bm25_current" | "bm25_rebuilding"` + conditional `lexical_index_note` (guaranteed key in `envelope_keys`, conditional note never is — the `embedding_mode`/`embedding_note` contract shape; parity fixture `canned_search` updated in the same commit).
- **Vector (DuckDB):** default exact scan — `ORDER BY array_cosine_distance(embedding, $q::FLOAT[k]) LIMIT n` with tenant/dimension predicates; deterministic, transactional, no index management. Opt-in `retrieval.vector_index: hnsw` builds `CREATE INDEX … USING HNSW (embedding) WITH (metric='cosine')` when `LOAD vss` succeeds (experimental upstream: in-memory default, persistence crash-unsafe — doctor warns when on; startup probes index presence and rebuilds if absent). Matryoshka two-tier ports via the ARRAY-slice probe — gated on `retrieval.matryoshka_enabled` (default off, requires `embedding.mrl_trained`), so the slice path is conditional in the arm. Envelope `vector_index_mode: "exact_scan" | "hnsw_ann"` + note.
- **Fusion:** both backends fuse through `handlers/query_fusion.py` (`rrf_fuse`, migration-024 total order `fscore DESC, cid ASC`). PG determinism suite is the proof; the SQL function remains only for `research/retrieval.py` **on Postgres** — on DuckDB that call site switches to the Python `rrf_fuse` (the function does not exist there, so no `UndefinedFunctionError` mapping is needed either).
- **Research retrieval:** four arms port onto the gateway + dialect (exact-scan dense arms replace halfvec probes; `SET LOCAL hnsw.*` is a PG-only shim no-op on DuckDB; citations/parents SQL is portable). Honesty keys where the engine differs. Analytics probes (`analytics_sql.py`) get DuckDB equivalents via the same shims.
- **Determinism:** any new score-ordered DESC clause in scanned files must end `, chunks.chunk_id ASC`; the exact-count audit in `tests/test_query_determinism.py` is updated in the same commit as any arm change. PG arm SQL stays byte-identical through the pure move; new DuckDB legs get their own determinism test file.

## 7. Ingest, command, tags, versioning parity

- **Ingest pipeline:** partition → chunk → embed (Ollama; pgml refused at config on duckdb with `embedding_note`) → extract (bert/gliner/regex/langextract; pgml refused) → store. `tools/ingest_store.py` ports to the gateway; the per-(tenant, file-hash) advisory lock keeps its held-connection semantics via the `advisory_lock_ctx()` escape hatch (§3.1 — DuckDB maps it to the single-writer lock); chunk-hash/tombstone/supersede logic is portable SQL. Hierarchy/shadow (`shadow_writer.py`, halfvec HNSW) ports to exact-scan equivalents. `ingest_watch` and reembed are gateway swaps.
- **Command flow:** `add_entity`/`add_relation` → `CommandHandler` → eventsourcing (SQLite event store on duckdb) → projections materialize entities/relations/documents/chunks through the gateway. Projection checkpoint/DLQ tables mirrored; replay semantics unchanged. `EventReader` reads lib-owned event tables that live in SQLite on the duckdb path — it ports via the eventsourcing recorder API, not the gateway (decided in W0 alongside the event-store leg).
- **Tags/versioning/router/idempotency/checkpoints:** gateway swaps; portable SQL confirmed per family in W0.
- **RAG hook:** `DuckDbRagBackend` over `chunks_vectors` keeps `rag_backend.ingest`/`retrieve` parity.

## 8. Coding domain parity

- `migrations_duckdb` mirrors the 12 coding tables; **append-only enforcement moves to the gateway** (`execute()` rejects UPDATE/DELETE on the protected list — DuckDB has no triggers), with PG triggers retained.
- Append-only-trigger migrations (012–015) translate to table DDL + gateway list entries; `codebook_versions` UNIQUE(tenant_id, sha256) dedupe and `ON CONFLICT` upserts are portable.
- pgml-gated pieces fall back to bert/gliner backends (existing selection ladder); reranking uses the configured non-pgml reranker. `exact_cache` (SHA-256-keyed, immutability) ports with gateway enforcement. BERTopic/inductive engine tables port; their SQL is audited in W0.

## 9. Swap UX (the "easily" part)

1. **Switch:** `database.backend: duckdb` + connection string (or `CORPUS_KB_DATABASE_BACKEND`). `validate_configs` + `doctor` verify the pair; PG-only engine selections fail fast with plain remediation.
2. **Move data:** `corpus-kb migrate-data --to duckdb:///path.duckdb` (and back). CLI-only. PG→DuckDB reads the source through the DuckDB **postgres extension** (`ATTACH 'postgres://…' AS src (TYPE POSTGRES)` — pushdown reads, no Python row loop; type mapping W0-verified, Python stream fallback kept); DuckDB→PG uses the Python stream copy (extension writes are row-by-row slow). Both directions include the event-store leg (§4) and verify per-table counts plus the SHA-256 corpus digest; mismatch → `ConflictError`.
3. **Setup without Docker:** `corpus-kb setup --backend duckdb` is the one-command path — it writes the config pair, installs the `duckdb` extra into the active environment, skips the compose/database/extensions phases, creates the DB file + migrations, and pre-installs `fts` (optional `vss`) with an offline note (extensions ship with the pinned LTS wheel line, `duckdb>=1.4,<2`).
4. **Doctor:** file writable, not locked by another process (plain-words single-writer explanation), FTS watermark age, extension availability, SQLite event-store health, pgml/AGE selections correctly refused — plus an engine line naming the active backend with the §1.2 sweet-spot suggestion.
5. **Concurrency rule (documented, enforced):** one process opens the database read-write; conflicting open raises `DatabaseUnavailableError` with remediation; CLI read paths go through server surfaces.

## 10. Sizing honesty

Exact-scan vector search and FTS rebuilds suit embedded corpora. DuckDB's own positioning — in-process, single-writer, analytical, poor fit for multi-client concurrent-write backends — matches exactly the local single-user case this engine targets. Documented guidance (STORAGE-BACKENDS.md): at the shipped **2560-dim** regime the working ceiling is **≤ ~100k chunks** (equivalent float budget to the earlier ~250k @ 1024 estimate), pending the benchmark gate, which records real numbers and updates this guidance — never guesses; the page also covers `memory_limit`/temp-directory tuning (out-of-memory on large blocking operators is DuckDB's documented failure mode at scale) and the one-process posture. Teams that outgrow it move to Postgres via `migrate-data` — both directions, no re-ingest. Research-scale tables benchmarked in the same gate.

## 11. CI green on first pass — hard rules

From the 25-item pre-mortem forecast; these bind every work package:

1. **Install bump timing:** the commit adding the first `import duckdb` also extends `ci.yml` installs to `.[dev,duckdb]` (type-check + test jobs) — CI never sees an unresolvable import. Dev-env contract: `pip install -e ".[dev,duckdb]"`.
2. **Config lockstep (5-file atomic, per key):** `get_default_config` + env map + `KNOWN_CORPUS_KB_ENV_VARS` + **both** `config.yaml` files (semantic-equality test `test_config_resources.py` — reformatting-only edits are safe) + `RUNTIME_CONFIG_KEYS` + ADMIN.md row. Keys: `database.backend`, `CORPUS_KB_DATABASE_BACKEND`, `graph.max_hops`, `retrieval.vector_index`, `retrieval.fts_rebuild_debounce_s` — new keys follow the **`retrieval.*` namespace v2** (`search.*` spellings are normalized aliases now).
3. **Catalogue-birth atomity:** row + `LEGS` entry + shared leg + **all four surfaces** (MCP/CLI/HTTP/socket — parity asserts the set) in one commit; `envelope_keys` = guaranteed keys only (conditional notes never; `trace_id` rides the shared envelope since #121); MCP docstrings first sentence ≤25 words.
4. **Registry:** no `surface_registry.SURFACES` changes; ghost auto-approve names kept; `VALID_TOOL_NAMES` + editor configs updated identically when exposing tools.
5. **Determinism pins:** `tests/test_query_determinism.py` file-scans specific modules (including `research/retrieval.py` and `analytics_sql.py` clause counts) — SQL moves update the scan list and the `query_handler.py` fusion pin docstring in the same PR; new DESC clauses carry `chunks.chunk_id ASC`. **Predicate-append convention:** the tenant predicate is the last WHERE clause and its param the last bound arg, so `$1`-anchor pins (e.g. the `chunk_id = \$1` regex) stay stable; any SQL-text edit also updates the stub SQL-substring dispatch (`rrf_fusion`, `pgml.embed`, `plainto_tsquery`, `pg_extension`) in the same commit.
6. **Lint:** line-length 100 (SQL constants ≤92, no f-string interpolation in long lines, `textwrap.dedent` + bound params); CI pins `ruff==0.15.18` — pin pre-commit to match or accept the drift knowingly.
7. **Complexity:** xenon `--max-absolute C`; SQL in module constants, one tiny builder per hop count, `migrate_data` split into `_copy_table`/`_corpus_digest`/`_verify_counts`; radon pre-check before push.
8. **Dead code:** ship fields/methods with consumers in the same commit, or extend the existing `tests/whitelist.py` sections (GraphStore interface, dataclass fields, Typer commands).
9. **Dependencies:** plain function-local `import duckdb` (deptry-visible; never `importlib` strings); duckdb **never** in base `dependencies` (license-lock churn); `uv.lock` regenerated in the same commit as the pyproject extra; **the same commit teaches deptry the extra** (per-rule ignore — otherwise DEP004 fires on the first `import duckdb`), verified by `poe deps`.
10. **Types:** pyright basic with current downgrades; `Row` adapter fixes the asyncpg-Record vs duckdb-tuple mismatch; pyright is unpinned in CI — spike the duckdb surface and consider pinning.
11. **Errors:** reuse `DatabaseUnavailableError` / `MigrationMissingError` / `ConflictError` with per-raise-site remediation; no new class without an `ERROR_MAP` row + taxonomy test.
12. **Packaging:** `"migrations_duckdb/*.sql"` in package-data in the same commit as the first migration file.
13. **Windows:** `duckdb_path_to_conn_string()` helper with drive-letter test at birth; fixtures always `close()`; never remove an open DB; never mix read_only modes on one path.
14. **Extensions:** `INSTALL fts` failure → `pytest.skip` (not error); pyproject floor **`duckdb>=1.4,<1.5`** (narrowed from `<2` by W0 2026-10-19: `json_set` absent through 1.5.6 and `date_trunc` return type drifts DATE→TIMESTAMP across the line — the 1.4 LTS point is the compatibility contract; fts/vss are core extensions there), recorded and pinned.
15. **Plain language:** error remediations one sentence ≤20 words, no banned idioms; docs ≤20-word sentences, FK ≤12 (`docs/*.md` in scope; `docs/research/**` exempt). String bank (use verbatim): "Set database.backend to postgres, or remove the graph.backend setting." / "Another program is using this database file. Close it, then try again." / "PostgresML runs only on Postgres. The Ollama embedder will be used instead." / "PostgresML reranking runs only on Postgres. The Ollama reranker will be used instead." / "PostgresML extraction runs only on Postgres. The regex extractor will be used instead." / "The copied data did not match the source. Fix the cause, then run migrate-data again." / "The text index is being rebuilt. Recent chunks may be missing from results." / "Exact scan is in use. Results are exact; large corpora will be slower." / "Use unqualified table names on DuckDB. Remove the corpus. prefix."
16. **Commits/branches:** single-line conventional commits (`feat(storage): …`, ≥10 chars, no bullet bodies — every `%B` line is validated); work branches are local-only `feature/sp-<wp>` slugs; per-phase integration branches `feature/sp-phase-X` are pushed and CI-polled, then squash-merged to `master` via PR (amended 2026-10-19 from direct master pushes — same intent: the per-phase local gate plus branch CI are the first-pass protection, and merge to master happens only on green).
17. **W0 spikes (before any port):** DuckDB `$n` placeholder binding; Windows DSN shapes; in-process connection cache/read_only mixing; extension channel reachability from runners; `array_cosine_distance` name/semantics + FLOAT[k] insert typing; JSON round-trip key order; UUID-as-VARCHAR envelope stability; `gen_random_uuid()`; eventsourcing.sqlite env mechanics + singleton keying; pyright surface; radon grades of builder prototypes; portable-SQL audit of one representative query per module family (automated by sqlglot, §17); cover-diff accounting with `importorskip`; DuckDB statement-failure semantics inside an open transaction (nested-transaction no-op rationale); postgres-extension type mapping for the `migrate-data` ATTACH path (JSONB, UUID→VARCHAR, FLOAT arrays); per-table tenancy audit (which tables lack `tenant_id` — feeds the manifest); event-store copy mechanics (eventsourcing API vs re-project). **+ 2026-10-19 addendum spikes** (issue #127 research comment; wiki `duckdb-port-predicted-failures-issue-127-grounding-2026-10-19.md`): `json_set` presence per 1.4.x/1.5.x (decides the pin floor) + merge_patch shim semantics; `match_bm25` bound-parameter binding + query-sanitizer corpus; zero-vector cosine clamp behavior + `norm>0` guard; `default_null_order` DESC placement; transaction-conflict error capture for the gateway retry loop; executemany-vs-Appender microbench at 2560 dims; extension offline vendoring + `autoinstall_known_extensions=false`; ATTACH halfvec→VARCHAR re-parse + CTAS-drops-constraints confirmation.

**Rules 18–24 (added by the v2 re-lens review):**

18. **Tenant-exemption manifest:** a frozen table→reason list (§3.1). One test asserts every SQL constant carries exactly one tenant predicate or touches only manifest tables; the same manifest drives `migrate-data`'s inventory and the schema-parity gate.
19. **FakeSqlGateway at birth:** ships in `tests/mock_pg.py` with sp-gateway (phase B), before any port lands — every ported module's hand-rolled `_ScriptedConn`-style fakes adopt it. `tests/mock_pg.py` and `tests/whitelist.py` join the §13 chokepoint list.
20. **Service enablement shape:** PG service on the **ubuntu test leg only** (GitHub `services:` are Linux-only — macOS runners have no Docker, Windows doesn't support service containers), using the pinned bundled GHCR image (stock `postgres:17` lacks the vector/age/pgml extensions migrations require). `tests/research_db.py` becomes env-derived (it hardcodes `localhost:5432` + superuser today — once the service exists the skip-gate passes and the fixture would error). Ubuntu leg timeout ≥20 min. The service lands in **phase B**, not F: 53 `requires_postgres` marks have never executed in CI, and discovering them in the final phase would misattribute pre-existing failures to the harness WP.
21. **`get_app` lifecycle:** keyed cache plus `close_app(key)` with eviction actually closing the evicted app (today `reset_app` only Nones the global, leaking pools). Parity fixture teardown order: close apps → close gateways → delete files (Windows `PermissionError` otherwise).
22. **`research_db` guard:** a unit test asserts the HNSW-strip regex in `tests/research_db.py` still matches `schema.sql` (silent session-fixture breakage otherwise).
23. **Session fixture ordering:** the backend-parametrized duckdb fixture takes its path as a fixture argument (never the env var, which `research_dsn` mutates) and finalizes before `research_dsn` drops its throwaway DB.
24. **Event-store leg decided in W0** before sp-migrate-data starts (copy-through-the-eventsourcing-API vs re-project); the schema-parity manifest exempts library tables explicitly.

## 12. Full-parity proof is CI-enforced

- **`tests/test_full_parity.py` (the centerpiece):** one golden fixture corpus; every catalogue op plus research/coding/release happy paths executed against BOTH backends; envelopes diffed byte-wise (deterministic ordering pinned). Backend-parametrized fixtures live in a dedicated module (conftest.py stays single-owner) and follow the session-ordering rules (§11 rule 23). **CI gains a Postgres service container on the ubuntu test leg only** (rule 20: GitHub services are Linux-only; bundled image pinned; timeout raised; service + env-aware `research_db` land in phase B so the 53 live PG marks burn in early); Windows/macOS run the DuckDB legs and keep auto-skipping PG. Local runs without Docker keep auto-skip.
- **Schema-parity gate:** `poe schema-parity` (+ CI job + zero-drift map row) parses the `CREATE TABLE` statements of BOTH migration sets with **sqlglot** (§17) and diffs column maps — no hand-maintained column list to drift; manifest tables (§3.1) are exempted explicitly. Drift fails.
- **Benchmark gate:** exact-scan latency/recall numbers recorded into `docs/research/benchmarks/`; STORAGE-BACKENDS.md guidance cites them.
- "Full feature parity" is then a CI fact: all legs green on both backends, three OSes.

## 13. Parallel execution schedule

Ground rules (from the parallelization DAG + program lessons; amended 2026-10-19, see decision log): background dispatch only; **≤5 implementation agents in flight sustained** (raised from 4: Phase A/B work packages are file-disjoint and the no-upstream-dep WPs `sp-es-sqlite` → phase A and `sp-migrate-data` handler → phase B are pulled forward); one read-only reviewer agent and one integrator agent ride alongside each wave — total in-flight ≤7; strict file ownership (no two concurrent agents touch one file); worktrees under `.worktrees/<wp>/`, branches `feature/sp-<wp>`, merged by a per-phase integrator in order **contracts → config → storage → handlers → surfaces → tests/docs**; `poe check` + full pytest green on the integration branch before every push; stray worktree verification by blob hash; never `git stash`; `poe gen` (harness regen) only at integration.

| Phase | WP (branch suffix) | Owns (disjoint) | Done-when |
|---|---|---|---|
| **0 Spikes** (2 agents) | sp-spikes-sql | W0 spike list §11.17 → findings appended to this doc's decision log | All spikes resolved or converted to per-WP tasks |
| **A Foundation-1** (4) | sp-config-truth | config.py, both YAMLs, validate_configs.py, doctor_env.py, ADMIN.md row, config tests | `poe lint types` + config/validate tests + `poe ste` |
| | sp-duckdb-extra | pyproject extras + deptry mapping + uv.lock | `poe deps lint` + lock resolve |
| | sp-graph-catalogue | routes_graph.py ORDER BY/tenant fix, graph_handler ordering, parity shared-leg prep (no new ops yet) | parity deep test stays green |
| | sp-query-fusion | handlers/query_fusion.py, query_handler.py switch, determinism audit update | determinism suite green unchanged-or-updated |
| **B Foundation-2** (3) | sp-gateway | storage/gateway.py, **CI install bump + PG service on ubuntu leg + env-aware `tests/research_db.py` + FakeSqlGateway in tests/mock_pg.py**, package-data glob, conftest duckdb fixtures, path helper + tests | gateway tests + type-check green with duckdb; live PG marks burn in |
| | sp-dialect | storage/dialect.py + unit tests (PG outputs byte-compared to today's strings) | dialect tests |
| | sp-duckdb-migrations | migrations_duckdb/*, _setup/migrate.py dispatch, migration tests | migrations apply twice cleanly (hermetic) |
| **C Seams** (4) | sp-es-sqlite | domain/application.py, es tests (may start during A/B — no upstream deps) | hermetic sqlite event-store tests |
| | sp-hops | storage/graph_sql.py + GraphStore ABC + PG/AGE/DuckDB impls (one commit) + graph_handler neighbors/traverse + cli.py + socket.py + mcp_tools.py wiring + graph.max_hops config lockstep + the four catalogue births, one commit per op (four surfaces each) | parity legs ×4 surfaces + determinism + fixture equivalence |
| | sp-duckdb-store | storage/duckdb_store.py, server_wiring branch, errors ERROR_MAP rows, test_graph_wiring leg, tests/test_store_parity.py (upstreams sp-gateway + sp-duckdb-migrations complete) | store parity golden legs |
| | sp-search-backend | storage/search_backend.py, handlers/query_search_pg.py pure move, contracts/envelopes.py keys, honesty-key tests | determinism pins hold; honesty tests |
| **D Core ports** (4) | sp-duckdb-search | storage/duckdb_search.py (+duckdb_fts.py), QueryHandler dispatch, embedder_selection notes, duckdb determinism file. Serves `query`, `ask` (#116), and the `/v1` adapter through the handler seam — no direct storage callers, so the UI stays storage-blind | honesty envelope tests + benchmark scaffolding |
| | sp-ingest-port | tools/ingest_store.py, ingest_reembed.py, shadow_writer.py, ingest_watch.py, hierarchy paths, **ingest_common.py (advisory lock), ingest_tools.py, ingest_surface_tools.py, ingest_embed.py, ingest_extract.py (inline set_config)** | hermetic duckdb ingest E2E |
| | sp-projections-port | projections/ (embed, documents, checkpoint, dlq, event reader — EventReader via the recorder API on duckdb, §7) | projection catch-up symmetry on duckdb |
| | sp-command-port | CommandHandler, IdempotencyChecker, TagHandler, VersioningHandler, RouterHandler, query_crud, parent_expand, DuckDbRagBackend (done-when scoped to command+event legs; projection-materialization assertions belong to sp-projections-port) | command E2E hermetic legs |
| **E Domain ports** (4) | sp-research-port | research/retrieval.py, research stores, projections/research/*, analytics_sql.py, batching, exact_cache, inductive (consumes sp-coding-port's gateway list entries — never edits gateway.py) | research happy paths on duckdb (parity legs) |
| | sp-coding-port | coding/* stores + **sole owner of gateway.py append-only list edits** | coding happy paths on duckdb |
| | sp-release-port | cli_release.py, report_governance.py, cli_research_views.py, cli_support.py | release/report legs |
| | sp-migrate-data | handlers/migrate_data.py (ATTACH path + events leg), cli.py adapter, migrate tests (hermetic + requires_postgres; may start during A/B — no upstream deps beyond the manifest) | round-trip digest verified both directions incl. event store |
| **F Gates+UX** (3) | sp-setup-doctor | _setup/install.py (**`setup --backend duckdb` flag + extra self-install**), doctor_storage.py, setup_steps/plan, doctor tests | doctor duckdb checks unit-tested; one-command duckdb setup works from a clean clone |
| | sp-full-parity-harness | tests/test_full_parity.py + golden corpus + duckdb matrix legs + **sole owner of ci.yml in F** (parity legs; the service itself landed in B) | all legs green locally both backends |
| | sp-suite-hardening | ste strings sweep, vulture whitelist extensions, xenon re-grade of ported functions | `poe check` full green |
| **G Closeout** (3 + integrator) | sp-schema-parity-gate | scripts/schema_parity.py (sqlglot-based, §17), pyproject poe target, ci.yml job (after F's harness merge), zero-drift map rows | gate trifecta (poe + CI + map row) |
| | sp-benchmark | docs/research/benchmarks/duckdb-hybrid-search.md | numbers committed |
| | sp-docs | docs/STORAGE-BACKENDS.md (**DuckDB-first quickstart**), ADMIN/API/INGESTION updates, README pointer, AGENTS.md line, harness regen | Vale + FK gates green |

Chokepoints (sequenced, never concurrent): `tests/test_surface_parity_deep.py` (A prep → C births), `contracts/envelopes.py` (C only), `conftest.py` (B only), `tests/mock_pg.py` + `tests/whitelist.py` (B birth, then adopters only), `pyproject.toml` (A→B→F), `server_wiring.py` (B, then F may touch), both `config.yaml` (always both, one commit), `query_handler.py` (A→C→D), `gateway.py` append-only list (E: sp-coding-port only), `.github/workflows/ci.yml` (B service → F legs — single owner per touch). Conflict inside a phase = process bug: huddle, don't force.

Dispatch is dependency-based under the global 4-cap: sp-dialect, sp-es-sqlite, and sp-migrate-data's handler module have no upstream deps and may start during A/B. Integration runtime stays tractable (~20–35 min per phase; the full suite does not run ×2 backends — only the parity harness parametrizes); pytest-xdist is deliberately NOT added mid-program (worker-isolation churn; spiked in G).

## 14. Zero-drift conformance map

| Rule | Compliance |
|---|---|
| 1. One op, one handler, many surfaces | New graph ops + migrate-data are handlers with thin adapters (migrate-data CLI-only by design — server holds the lock) |
| 2. Registry-first | Catalogue/LEGS changes precede handlers in every phase that touches surfaces |
| 3. One envelope per family | Guaranteed keys declared; conditional notes follow the embedding_mode shape |
| 4. Errors via taxonomy | Gateway/duckdb errors map into `ERROR_MAP`; no catch-alls |
| 5. Layering | SearchBackend protocol lives in `storage/` (implementations import downward); gateway in `storage/`; handlers never import drivers. Import-linter pins it: storage is forbidden `_setup` (the installer edge), while handlers -> `_setup` stays the sanctioned bootstrap edge (apply_schema, migration runner; lazy imports in migrate_stream/migrate_roundtrip) |
| 6. Plain English | String bank §11.15; Vale + `poe ste` gates |
| 7. Size/hygiene | New files ≤250 lines (§3.4); xenon re-grades on every port; vulture whitelist discipline |
| 8. poe gates | Every WP ends at its listed done-when; integrator runs `poe check` |
| 9. Deterministic output | Total orders pinned everywhere; parity harness byte-diffs envelopes |
| 10. Gate trifecta | `schema-parity` lands with poe target + CI job + map rows; full-parity harness is a test-matrix job |

## 15. Risks

| Risk | Mitigation |
|---|---|
| Program size (~30 modules ported) | Mechanical port pattern (gateway swap + SQL audit per module) + full-parity harness catches drift per phase, not at the end |
| DuckDB SQL-compat gaps found mid-port | W0 per-family audit first; per-module twin fallback for anything that cannot shim |
| Exact-scan performance at research scale | Benchmark gate with committed numbers; honest notes; Postgres guidance for larger corpora |
| Single-writer surprises | Enforced `DatabaseUnavailableError` + plain remediation; server owns the lock; documented posture |
| Determinism drift between engines | Shared `graph_sql.py` makes order drift structural-impossible for traversal; `rrf_fuse` single implementation; parity harness byte-diffs |
| FTS staleness | Watermark + envelope honesty + debounced rebuild; rebuild cost benchmarked |
| vss corruption on crash (upstream) | Opt-in only; exact scan default; doctor warning |
| CI first-pass | §11 rules bind every WP; per-phase local `poe check` before the only push (master) |
| Parity entropy — the Mattermost failure mode: multi-backend support decays without enforcement and ends in a retreat | The full-parity harness + schema-parity gate run on every push **forever**; Grafana sustained multi-backend with the same enforcement-first shape. If the cost ever proves unsustainable, the documented fallback is a deliberate narrowing to one engine — never silent drift |
| DuckDB memory pressure on large blocking operators (documented OOM mode at scale) | Sizing guidance + `memory_limit` tuning docs + benchmark gate; the `migrate-data` path to Postgres is the pressure valve |

## 16. Decision log

| Decision | Rationale | Date |
|---|---|---|
| Full feature parity across all UX areas in v1 | User directive; read-mostly scoping rejected | 2026-10-14 |
| `SqlGateway` is the one seam | 2-adopter version was over-engineering; ~30 adopters make it the elegant core — one protocol instead of ~30 duplicated SQL twins | 2026-10-14 (pre-mortem) |
| Explicit tenant predicates in all SQL; RLS retained on PG | Uniform SQL text on both engines; RLS becomes defense-in-depth, not the only fence | 2026-10-14 |
| `BackendCapabilities` module cut | 8 of 10 flags had zero consumers; backend-mix rejection = pure config function; honesty keys emitted by the impaired component | 2026-10-14 (pre-mortem) |
| No StorageBackend façade | Two sibling factories match the flat composition style; façade couples consumers that share nothing | 2026-10-14 (pre-mortem) |
| duckpgq unused; recursive CTE + fixed joins | Community extension risk for zero UX gain at 1–N hops | 2026-10-14 (research) |
| Exact-scan default, vss opt-in | Upstream experimental status (in-memory, crash-unsafe persistence) | 2026-10-14 (research) |
| FTS managed rebuild | Upstream has no auto-refresh | 2026-10-14 (research) |
| eventsourcing.sqlite for the event store | Ships in-library; same `PERSISTENCE_MODULE` mechanism already in use | 2026-10-14 (research) |
| migrate-data CLI-only | Server holds the DuckDB write lock; HTTP surface would require server-down semantics | 2026-10-14 (pre-mortem) |
| Ghost auto-approve names kept | They become real catalogue tools; removal churns 4 editor configs + generated codex.json for no gain | 2026-10-14 (pre-mortem) |
| Gateway protocol final shape: fetch/fetchrow/fetchval/execute/transaction; PG status-tag synthesis in `execute`; nested-tx depth-counter no-op; graph stores stay off the gateway | v2 re-lens: 25+ `fetchval` sites, `_common.py` parses status strings, SAVEPOINT fail-open contracts, session-GUC semantics | 2026-10-14 (review) |
| `migrate-data` gains an event-store leg; PG→DuckDB path = postgres-extension ATTACH | Table-only copy silently drops command history; the extension deletes the copy-loop failure modes for that direction | 2026-10-14 (review) |
| Schedule re-slot: sp-hops merged into one WP in C; sp-duckdb-store moved B→C; sp-schema-parity-gate moved F→G; PG service enablement moved F→B | Phase A would have ended CI-red (catalogue birth needs surfaces needs the hop ABC); two ci.yml/gateway.py same-phase collisions; 53 never-run PG marks must burn in early | 2026-10-14 (review) |
| OSS gate: **sqlglot** (static analysis + schema-parity + tenant-coverage gates) and the **postgres extension** (migration copy) adopted; SQLAlchemy Core, duckdb-engine, alembic, ibis, data-diff, pgloader rejected | §17 | 2026-10-14 (review) |
| Version pin **`duckdb>=1.4,<2`** (LTS line) | 1.4.0 LTS (2025-09) carries one-year support, core-extension fts/vss, encryption, MERGE | 2026-10-15 (stress test) |
| `WITH RECURSIVE … USING KEY` = optional DuckDB accelerator, not the contract | Exact BFS-min-depth fit but DuckDB-only syntax; the portable classic CTE keeps one shared builder and the determinism pins | 2026-10-15 (stress test) |
| Default backend stays `postgres`; engine-selection policy documented instead (§1.2) | The goal is an easy swap, not a default flip; Grafana's embedded-default precedent informs the light-local path without breaking existing installs | 2026-10-15 (stress test) |
| pg_duckdb, DuckLake, MotherDuck rejected | Opposite direction (analytics inside PG, not a containerless local path); concurrent-writer machinery for a constraint we avoid by posture; cloud contradicts the README's local-only promise | 2026-10-15 (stress test) |
| **v3.1 repo re-base (master 6fe7221 → fa02dd7)** | 22 commits landed while the plan was in review; facts re-based: 4-surface parity mandate, halfvec(2560) + `retrieval.*` namespace, ask + `/v1` + NiceGUI UI + otel surfaces, migrations 025/026, llamacpp reranker, 2560-dim sizing | 2026-10-17 |
| **Research addendum grounding (9 parallel legs)** | Pre-W0 grounding: `json_set` absent in 1.4 (shim via `json_merge_patch`), DuckDB aborts conflicting writers (gateway retry loop), zero-vector cosine clamps to 2.0 vs PG NaN-last (norm>0 guard), DESC NULLS placement flip (`default_null_order`), vss LOAD-before-ATTACH WAL replay + experimental-persistence flag, fts/vss/postgres are downloads (CI warm-cache + doctor probes), migrate-data VARCHAR re-parse + CTAS-drops-constraints, `SQLiteApplicationRecorder` lacks `subscribe()` (polling EventReader), executemany per-row (Appender batches), migration inventory 15 fns/9 triggers/23 policies/42 GUC sites/1 SERIAL+9 IDENTITY. Full citations: issue #127 comment 2026-10-19 + wiki page | 2026-10-19 |
| **Parallelism cap 4 → 5 implementation agents; sp-es-sqlite → A, sp-migrate-data handler → B** | Execution-plan amendment: Phase A/B WPs verified file-disjoint; no-upstream-dep WPs pulled forward to remove idle capacity; reviewer + integrator agents add capacity without ownership conflicts | 2026-10-19 |
| **Rule 16: direct master pushes → per-phase integration branch + PR** | Repo practice is PR-merged master; branch CI + local `poe check` preserve the first-pass intent with safer governance | 2026-10-19 |
| **W0 verdicts (full evidence: `findings/*.md`, 4 spike branches)** | (1) Pin narrowed `<2`→`<1.5`: `json_set` absent through 1.5.6 (two-step `json_merge_patch` shim verified for all 4 `jsonb_set` sites), `date_trunc` returns DATE@1.4 vs TIMESTAMP@1.5. (2) Gateway: conversion errors ABORT a tx (parser/catalog errors do not) → rollback-and-replay path; conflict predicate = `TransactionException` + "Conflict on update!" raised at UPDATE, 5 retries; catch `IOException`/`TransactionException` BEFORE `OperationalError` (they subclass it); `duckdb:///` scheme stripped by the path helper; `enable_progress_bar` defaults ON without a tty and is SET-per-session only; `default_null_order='postgres'` (exact PG null placement) per connection. (3) Search: `match_bm25` binds `$1` in the 1.4 scalar-macro shape only; conjunctive+stopwords bug PRESENT in 1.4.5 → OR-mode + client sanitizer; zero-vector = sim −1.0/dist 2.0, guard `sqrt(array_inner_product(v,v))>0` (no `array_norm`); vector params need `$1::FLOAT[2560]`; batch inserts via `con.append`/INSERT-SELECT ≈18× row-by-row. (4) Migrations: tenancy manifest CONFIRMED CLEAN (40 tables, 37 tenanted; only legacy `corpus.*` exempt). (5) Event store: copy-through-API (live volume: 2 events); SQLite `stored_events` rowid = notification id; no `subscribe()` → polling via `select_notifications` (~70–90 lines). (6) ATTACH: read-only verified live; CTAS drops ALL constraints → DDL-recreate-then-INSERT; `halfvec`→VARCHAR re-parses via `replace(text,' ','')::FLOAT[2560]`; unbounded `numeric`→DOUBLE (9 live columns). (7) sqlglot parses all 263 statements but is semantically permissive → schema-parity/audit gate must be a construct flagger (families seeded: 67 DO-blocks, 42 FORCE RLS, 20 policies, 12 vector(n)). (8) FTS rebuild ≈0.5s/5k docs — watermark design cheap. (9) Fixtures close in reverse order (rare threaded-teardown segfault observed 1/6) | 2026-10-19 |
| **Wave B blocker: reject `corpus.`-qualified SQL on the DuckDB path — typed guard, never rewriting** | DuckDB names the catalog after the file stem: `corpus.t` silently works on a file named `corpus.duckdb` and fails with a raw catalog error on any other name. `DuckDbGateway.fetch`/`execute` raise the existing `ValidationError` (rule 11 — no new class) with the banked §11.15 string before executing. Quoted regions (single- and double-quoted, `''` escapes) are masked before the token scan, so literal data can neither trip nor dodge the guard. The Postgres path is untouched (`corpus.` is correct there); Wave D/E ports drop the prefix at their call sites | 2026-10-20 |
| **Phase C carry: `:memory:` DSN strip** | `duckdb_conn_string_to_path` strips the leading slash before `:memory:` (a URL-decoded `duckdb:///:memory:` otherwise joins the cwd drive letter on Windows and creates a stray file). Carried into the integration branch's decision log so the helper's contract is declared, not folklore | 2026-10-20 |
| **PG projections catch-up reads via the recorder API** | Catch-up on Postgres now reads through the eventsourcing `select_notifications` recorder API instead of the legacy direct SELECT over the lib-owned events table. Verified equivalent against the lib contract (same `notification_id` ordering, `inclusive_of_start=False` keeps the strict greater-than position); one read path now serves both engines | 2026-10-20 |
| **Chunkless relations: delete+insert replaces the dead pre-008 upsert** | Migration 008 widened the unique key with `chunk_id`, so the narrow-key `ON CONFLICT` upsert matched nothing and every `Relation.Created` landed in the DLQ on both engines. Delete-then-insert of chunkless rows in one scope restores the dedupe portably; the tenant advisory lock (`projections/_locks.py`) closes the READ COMMITTED window where two writers both DELETE 0 and both INSERT (NULL `chunk_id` never meets the unique index) | 2026-10-20 |
| **Declared ingest-time read divergences on DuckDB** | The dependent vector/relation deletes commit in a second pass after the chunk rewrite, so a concurrent reader can see live chunks with no vectors (or stale relations) mid-ingest; one transaction cannot span both passes given the engine's write path. Same declaration covers the tombstone pass (a shrunken document's tombstones land at the commit, not per row) and `document_date` first-insert-only. Operator note: `docs/ADMIN.md` "DuckDB engine notes". All three settle at the ingest commit | 2026-10-20 |
| **DuckDB RAG backend zero-query semantics: sentinel rows, not abstain** | The user-facing query arm abstains its vector leg on a zero query embedding (`zero-query-embedding` reason). `DuckDbRagBackend` keeps answering: a zero query lands every row on the `-2.0` sentinel in deterministic id order, because the RAG caller fuses both arms and needs candidate rows. Declared divergent on purpose; `duckdb_rag` docstring + `tests/test_duckdb_rag.py` pin it | 2026-10-20 |
| **Reembed scan: PG gains the chunk-id tiebreaker (engine parity)** | `SCAN_SQL_PG` ordered by `cv.embedded_at NULLS LAST` alone, so same-timestamp batches (one bulk re-embed writes one `NOW()`) could permute between runs — zero-drift rule 9. The DuckDB arm always carried `cv.chunk_id ASC`. PG now orders `embedded_at NULLS LAST, cv.chunk_id ASC`, shape-equal to the DuckDB arm, so the repair order under `LIMIT` is stable per engine. Pure ordering change — no row selection changes. The SQL lives in `tools/ingest_sql_text.py` (250-line split of `ingest_sql.py`) with the declaration comment beside it | 2026-10-20 |
| **Legacy-event drain resolution: owner read from the Ingested event, never a backfill (issue #143 population)** | Events persisted before the `ChunksAdded` tenant stamp carry no `tenant_id`, so the foreign-tenant skip never fired: the drain replayed the foreign text under the draining tenant and the chunks FK (doc_id-only) burned the 20-pass loop on every catch-up. The event store is append-only, so history is not rewritten; instead the drain resolves the owner from the aggregate's Ingested event for the same `originator_id` (`EventReader.owner_tenant`: recorder `select_events` or a first-event SELECT), and only an unresolvable event falls back to the declared default tenant. The originator id normalizes to UUID first — the SQLite recorder stores `originator_id.hex`, so a dashed string never matches | 2026-10-20 |

## 17. OSS tooling gate (2026-10-14 review)

Gate question: do open-source frameworks or tools make this implementation easier and more reliable? Verdicts are grounded in the sources below; every adoption is a W0-verified swap-in, never a design dependency.

| Tool | Verdict | Where it lands | Why |
|---|---|---|---|
| DuckDB **postgres extension** (`ATTACH … (TYPE POSTGRES)`) | **Adopt** | `migrate_data` PG→DuckDB leg | The copy becomes `CREATE TABLE … AS SELECT` over a pushdown-read attached source — deletes the row-loop code and its failure modes for that direction. Type mapping (JSONB, UUID, FLOAT arrays) W0-verified; Python stream fallback kept; DuckDB→PG stays on the Python loop (extension writes are row-by-row slow). |
| **sqlglot** (Apache-2.0, dependency-free) | **Adopt** | W0 portable-SQL audit; `schema-parity` gate; tenant-predicate coverage test (rule 18) | **Static analysis only — never a runtime transpiler** ("a transpiler, not a validator"; determinism pins forbid rewriting PG SQL). It parses every SQL literal to flag non-portable constructs (a repeatable gate instead of a manual audit), diffs `CREATE TABLE` column maps across both migration sets, and counts tenant predicates per statement. Battle-tested at scale (Fivetran); first-class DuckDB dialect. |
| **testcontainers-python** | **Consider (conditional)** | full-parity harness local PG fixture | The env-aware `research_db` fix (rule 20) is required regardless; testcontainers is the fallback if the env-DSN path proves flaky locally. Not a CI dependency (CI uses the service container). |
| **duckdb-engine** (SQLAlchemy dialect for DuckDB) | **Reject** | — | Mature and widely used, but nothing in the design needs SQLAlchemy; sqlglot covers the schema-parity need without a driver-layer dependency. |
| SQLAlchemy Core as the data-access layer | **Reject** | — | Determinism pins require byte-identical PG SQL; GUC/RLS session semantics and asyncpg patterns do not map onto Core; the ~200-line gateway is the right weight for ~30 adopters and one protocol. |
| alembic / ibis-project / data-diff / pgloader | **Reject** | — | alembic replaces a working runner for no gain over the sqlglot schema gate; ibis = full rewrite of the SQL layer; data-diff duplicates the corpus digest; pgloader is the wrong direction and CI-hostile. |
| pytest-xdist | **Reject mid-program; spike in G** | CI runtime | Would cut the ~20-min integration barrier 3–4× but adds worker-isolation churn (duckdb file fixtures, determinism ordering, fake-gateway threading) mid-program. |
| **pg_duckdb** | **Reject** | — | DuckDB-official but the opposite direction: DuckDB analytics *inside* Postgres. It does not give users the containerless local path, and our postgres-extension use (the migration copy) already covers the one place the two engines must meet. |
| **DuckLake** / **MotherDuck** | **Reject** | — | DuckLake solves concurrent writers — a constraint we avoid by posture (single-writer local) and answer with Postgres at scale. MotherDuck is a cloud service; the README promises local-only. |

## 18. Sources

- [DuckDB FTS extension](https://duckdb.org/docs/current/core_extensions/full_text_search.html) · [DuckDB VSS extension](https://duckdb.org/docs/current/core_extensions/vss.html)
- [DuckDB concurrency](https://duckdb.org/docs/current/connect/concurrency.html) · [Multiple Python threads](https://duckdb.org/docs/lts/guides/python/multiple_threads.html)
- [duckpgq community extension](https://duckdb.org/community_extensions/extensions/duckpgq.html) · [duckpgq.org](https://duckpgq.org) · [Graph queries guide](https://duckdb.org/docs/lts/guides/sql_features/graph_queries.html)
- [eventsourcing — persistence modules](https://eventsourcing.readthedocs.io/en/latest/topics/persistence.html)
- [DuckDB PostgreSQL extension](https://duckdb.org/docs/current/core_extensions/postgres/overview.html) · [sqlglot](https://github.com/tobymao/sqlglot) · [duckdb-engine](https://pypi.org/project/duckdb-engine) · [testcontainers-python guide](https://testcontainers.com/guides/getting-started-with-testcontainers-for-python)
- [Announcing DuckDB 1.4.0 LTS](https://duckdb.org/2025/09/16/announcing-duckdb-140.html) · [USING KEY in recursive CTEs](https://duckdb.org/2025/05/23/using-key.html) · [How DuckDB runs recursive CTEs faster (2026)](https://duckdb.org/2026/08/25/how-duckdb-runs-recursive-ctes-faster.html) · [DuckDB concurrency](https://duckdb.org/docs/current/connect/concurrency.html)
- [Grafana's multi-backend store pattern](https://community.grafana.com/t/advantage-s-of-switching-from-sqlite-to-postgres-mysql-backend/16518) · [Grafana HA setup](https://grafana.com/docs/grafana/latest/setup-grafana/set-up-for-high-availability) · [Mattermost drops MySQL](https://docs.mattermost.com/administration-guide/manage/admin/postgres-migration) · [pg_duckdb](https://github.com/duckdb/pg_duckdb) · [Kestra: when to leave local DuckDB](https://kestra.io/blogs/duckdb-vs-motherduck)
- Pre-mortem (2026-10-14): CI first-pass forecast (25 ranked findings), architecture critique, parallelization DAG — summarized in §2, §11, §13.
- Review pass (2026-10-14): v2 re-lens (15 new CI findings → §11 rules 18–24; schedule defects fixed in §13) + OSS tooling gate (§17).
- Stress test (2026-10-15): session-goal ledger and verdicts (§0), engine-selection positioning (§1.2), capability refresh against DuckDB 1.4 LTS (§2), product-UX swap gaps closed (§9), parity-entropy risk (§15).
- Repo re-base (2026-10-17): master 6fe7221 → fa02dd7 (config-alignment #101–#114, UX program #115–#124) — §0.1.
