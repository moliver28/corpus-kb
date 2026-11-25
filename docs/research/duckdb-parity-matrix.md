# DuckDB parity matrix

This document is the AUTHORITATIVE parity record for the two storage
backends. It states, for every config leaf and every operation: what
Postgres does, what DuckDB does, and what refuses with a typed error.
The user-facing summary lives in [STORAGE-BACKENDS.md](../STORAGE-BACKENDS.md);
this matrix is the source that document must match.

The matrix closes the DuckDB parity program (plan:
[parity-recovered-plan.md](parity-recovered-plan.md), PR-10). Date of
record: 2026-11-25, at the merged state of PR-9.

## Sources of truth and enforcement

The tables below are checked against code, not prose:

- `src/corpus_kb/config_backend_matrix.py` — one declaration row per
  config leaf (269 leaves). The `config-backend-parity` gate
  (`scripts/config_backend_parity_gate.py`, `poe config-backend-parity`)
  fails when either defaults source (`get_default_config()` or the
  packaged `config.yaml`) disagrees with the rows in either direction.
- `src/corpus_kb/ops_backend_matrix.py` — one declaration row per
  operation (70 ops). A test pins exact set equality with the parity
  catalogue (`tests/test_backend_operation_parity.py::PARITY_OPS` plus
  the HTTP-only `ingest_upload`).
- The gate greps each row's reader module for the row's key. A key no
  code reads turns the gate red (the dead-key check). A refusal row must
  also bind to `tests/test_backend_gates.py`, so a refusal cannot lose
  its test.
- A behavior that claims a refusal may not declare the key honored on
  the refused backend (consistency assert in the same gate).

Terms, one word each: the two storage engines are "backends". A typed
load-time error is a "refusal". A difference that stays because the
engines are different by design is a "designed delta".

## Fix-wave index

| Wave | GitHub PR | Merged | Scope |
|---|---|---|---|
| PR-1 | #572 | 2026-11-24 | Analysis date ops run on DuckDB (G01) |
| PR-2 | #573 | 2026-11-24 | Re-ingest of an ingested source is idempotent (G03) |
| PR-3 | #574 | 2026-11-24 | Read models advance after add-entity/add-relation (G02) |
| PR-4 | #575 | 2026-11-24 | 035 CHECKs mirrored, migrate/setup flags refuse, #527 P3s (G04, G07, G15, G27 subset) |
| PR-5 | #576 | 2026-11-24 | Embedding width parameterized in both migration sets (G28) |
| PR-6 | #577 | 2026-11-24 | Backend-mismatched keys refuse in both directions (G09, G16-G20) |
| PR-7 | #579 | 2026-11-24 | Dead-key parity: one key wired, five keys removed, lockstep completed (G05, G06, G21-G24, G29) |
| PR-8 | #585 | 2026-11-25 | Uncovered ops proven on DuckDB (G25, G26) |
| PR-9 | #587 | 2026-11-25 | config-backend-parity gate (rule 12 artifacts) |
| PR-10 | this PR | 2026-11-25 | This matrix, user-table sync, pins (G08, G10-G14, G27 tail) |

---

## 1. Config-leaf matrix

269 leaves, in declaration order. Classes:

- **honored** — the key behaves the same on both backends.
- **refused-typed** — a backend refuses the key with a named fix. The
  load raises `ValueError` (`config.py:283`). The graph factory and the
  boot guard raise `ConfigError` (`storage/graph_factory.py:123`,
  `_setup/version_guard.py:232`).
- **pinned-designed-delta** — both backends honor the key; one backend
  applies it in a narrower, documented way (section 4).
- **removed** — the key is gone from the shipped defaults (section 1.1);
  absent rows fail the gate in both directions, so a removed key cannot
  silently return.

Evidence per row: the reader module (the code that reads the key) and
the declaration in `config_backend_matrix.py`. The gate holds the
reader-and-key pairing green; the refusals hold the refused rows green.

| Keys | Postgres | DuckDB | Class | Declared behavior | Reader (evidence) |
|---|---|---|---|---|---|
| `analysis.method_register_path` `analysis.classification.enabled` `analysis.classification.min_labels_per_class` `analysis.classification.tier` `analysis.eecs.enabled` `analysis.labeling.enabled` `analysis.labeling.lfs_file` `analysis.labeling.min_signals` `analysis.routing.fallback_log_path` `analysis.routing.llm_fallback_enabled` `analysis.sentiment.lexicon_path` `analysis.topics.max_embeddings` `analysis.topics.over_time.bins` `analysis.topics.over_time.enabled` `analysis.topics.over_time.undated` | honored | honored | honored | honored | `analysis/settings.py` |
| `analysis.routing.confidence_threshold` `analysis.routing.margin` `routing.confidence_threshold` | honored | honored | honored | honored | `eval/scoring.py` |
| `analysis.sentiment.enabled` | honored | honored | honored | honored | `tools/sentiment_tools.py` |
| `ask.max_sources` | honored | honored | honored | honored | `ask/ask_settings.py` |
| `cache.deductive_enabled` `cache.enabled` | honored | honored | honored | honored | `research/deductive_run.py` |
| `cache.inductive_enabled` | honored | honored | honored | honored | `research/exact_cache.py` |
| `coding.coder` | honored | honored | honored | honored | `coding/coder_dispatch.py` |
| `coding.model` `llm.base_url` `llm.model` | honored | honored | honored | honored | `coding/coder_client.py` |
| `coding.repair_retries` | honored | honored | honored | honored | `coding/structured_output.py` |
| `coding.require_structured_enforcement` | honored | honored | honored | honored | `config.py` |
| `database.backend` `database.connection_string` `routing.enabled` `retrieval.self_query.enabled` `retrieval.self_query.model` `retrieval.self_query.base_url` `retrieval.self_query.timeout_seconds` `server.host` | honored | honored | honored | honored | `server_wiring.py` |
| `embedding.base_url` `embedding.batch_size` `embedding.max_batch_texts` `embedding.query_instruction` | honored | honored | honored | honored | `rag/embedder.py` |
| `embedding.dimensions` `embedding.late_chunking` `embedding.model` | honored | honored | honored | honored | `rag/embedder_selection.py` |
| `embedding.fallback_provider` | honored | honored | honored | honored | `tools/ingest_embed.py` |
| `embedding.mrl_trained` | honored | honored | honored | honored | `config_gates.py` |
| `embedding.provider` | honored | honored | honored | honored | `rag/embedder_factory.py` |
| `extraction.backend` `graph.extractor` `extraction.ensemble.enabled` `extraction.ensemble.members` | honored | honored | honored | honored | `extraction/__init__.py` |
| `extraction.mode` `graph.fixture_dir` `graph.live_fallback` | honored | honored | honored | honored | `extraction/ml_backend.py` |
| `extraction.cache.enabled` | honored | honored | honored | honored | `_setup/validate_extraction_ml.py` |
| `extraction.gliner.enabled` `extraction.gliner.model` `extraction.gliner.revision` `extraction.gliner.types` | honored | honored | honored | honored | `extraction/gliner_backend.py` |
| `extraction.relex.max_label_pairs` `extraction.relex.model` `extraction.relex.threshold` | honored | honored | honored | honored | `extraction/relex_backend.py` |
| `extraction.tune.batch_size` `extraction.tune.model` `extraction.tune.steps` `extraction.tune.learning_rate` `extraction.tune.lora_alpha` `extraction.tune.lora_dropout` `extraction.tune.lora_r` | honored | honored | honored | honored | `extraction/lora_tune.py` |
| `graph.backend` | honored (mix values type-refused at load) | honored (mix values type-refused at load) | honored + typed mix refusals | both-with-mix-rules | `backend_gates.py` |
| `graph.duckdb_graph_backend` | honored default; a tuned-off-default value type-refuses at load | honored | honored-duckdb-only | duckdb-only | `backend_gates.py` |
| `graph.max_hops` | honored | honored | honored | honored | `handlers/graph_handler.py` |
| `graph.model_version` `graph.prompt_version` | honored | honored | honored | honored | `tools/ingest_store.py` |
| `graph.ontology_path` `ingest.contextualize.enabled` `ingest.contextualize.enabled_source_types` | honored | honored | honored | honored | `tools/ingest_common.py` |
| `graph_policy.mode` | honored | honored | honored | honored | `storage/graph_policy.py` |
| `ingest.chunking.enabled` `ingest.chunking.child_tokens` `ingest.chunking.chunk_tokens` `ingest.chunking.parent_tokens` `ingest.chunking.overlap_tokens` `ingest.chunking.respect_boundaries` | honored | honored | honored | honored | `chunking/hierarchy_settings.py` |
| `ingest.contextualize.backend` `ingest.contextualize.model` `ingest.contextualize.prompt_profile` `ingest.contextualize.max_context_tokens` `ingest.contextualize.document_first_prompt` `ingest.contextualize.apply_to` `ingest.contextualize.base_url` `ingest.contextualize.fixture_dir` `ingest.contextualize.temperature` `ingest.contextualize.live_fallback` | honored | honored | honored | honored | `rag/contextual_settings.py` |
| `ingestion.concurrency` | honored | honored; always one ingest worker (single-writer file, by design) | pinned-designed-delta | honored-both-serial-by-design-on-duck | `tools/ingest_common.py` |
| `ingestion.default` `ingestion.strategies.survey` | honored | honored | honored | honored | `ingestion/registry.py` |
| `ingestion.strategies.pptx` | honored | honored | honored | honored | `ingestion/strategies/_pptx_parts.py` |
| `ingestion.strategies.transcript` | honored | honored | honored | honored | `research/transcript_parser.py` |
| `ingestion.strategies.web` | honored | honored | honored | honored | `web/__init__.py` |
| `installer.data_dir` | honored | honored | honored | honored | `_setup/install_state.py` |
| `installer.postgres_image` | honored | honored | honored | honored | `_setup/setup_common.py` |
| `installer.verify_image_signature` | honored | honored | honored | honored | `_setup/setup_stack.py` |
| `installer.profiles.minimal.llm` `installer.profiles.minimal.model` `installer.profiles.minimal.ram_gb_max` `installer.profiles.minimal.vram_gb_max` `installer.profiles.balanced.llm` `installer.profiles.balanced.model` `installer.profiles.balanced.ram_gb_max` `installer.profiles.balanced.vram_gb_max` `installer.profiles.performance.llm` `installer.profiles.performance.model` `installer.profiles.performance.ram_gb_max` `installer.profiles.performance.vram_gb_max` | honored | honored | honored | honored | `_setup/profiles.py` |
| `judge.enabled` | honored | honored | honored | honored | `tools/ask_tools.py` |
| `judge.base_url` `judge.max_claims` `judge.model` | honored | honored | honored | honored | `rag/judge.py` |
| `notebooks.enabled` `notebooks.directory` | honored | honored | honored | honored | `notebooks/settings.py` |
| `observability.enabled` `observability.otlp_endpoint` `observability.service_name` | honored | honored | honored | honored | `utils/tracing.py` |
| `queue.autorun` | honored | honored | honored | honored | `queue/autorun.py` |
| `queue.max_attempts` | honored | honored | honored | honored | `tools/queue_tools.py` |
| `queue.poll_seconds` | honored | honored | honored | honored | `cli/cli_queue.py` |
| `queue.stale_lease_seconds` | honored | honored | honored | honored | `queue/store.py` |
| `reranking.allow_remote_urls` `reranking.backend` `reranking.base_url` `reranking.calibration` `reranking.enabled` `reranking.llamacpp_url` `reranking.model` `reranking.score_floor` `reranking.timeout_seconds` `reranking.max_pair_tokens` | honored | honored | honored | honored | `rag/rerank_settings.py` |
| `reranking.candidates` `retrieval.expand_to_parent` | honored | honored | honored | honored | `handlers/query_stages.py` |
| `reranking.over_retrieve_n` | honored | honored | honored | honored | `handlers/query_vector_rows.py` |
| `reranking.return_top_k` | honored | honored | honored | honored | `rag/rerank_rules.py` |
| `research.alt_test.fdr_q` `research.alt_test.margin` `research.alt_test.method` `research.alt_test.n_bootstrap` `research.alt_test.passing_fraction` `research.audit.sample_rate` `research.audit.seed` `research.labeling.aggregation` `research.labeling.cv_folds` `research.labeling.flag_threshold` `research.labeling.min_signals` `research.prompt_stability.alpha_threshold` `research.prompt_stability.min_variants` | honored | honored | honored | honored | `coding/measurement_settings.py` |
| `research.consensus.min_run_fraction` `research.consensus.runs` `research.consensus.similarity_threshold` | honored | honored | honored | honored | `research/cycle_consensus.py` |
| `research.cycle.halt_on` | honored | honored | honored | honored | `research/cycle_gates.py` |
| `research.embedder.dimensions` | honored | honored | honored | honored | `_setup/doctor_research.py` |
| `research.inductive.centroid_drift_threshold` `research.inductive.entropy_threshold` `research.inductive.recluster_every_batches` `research.inductive.tau_dup` | honored | honored | honored | honored | `research/inductive_run.py` |
| `research.ingest.watch_interval_s` | honored | honored | honored | honored | `cli/cli_ingest.py` |
| `research.judgment_distribution.use_mean` | honored | honored | honored | honored | `coding/judgment_sampling.py` |
| `research.release.default_profile` | honored | honored | honored | honored | `research/release/profiles.py` |
| `research.release.enforcement` | honored | honored | honored | honored | `research/release/enforcement_settings.py` |
| `research.review.batch_size` `research.review.random_fraction` | honored | honored | honored | honored | `research/review_batch.py` |
| `research.review.diversity` `research.routing.confidence_level` `research.routing.min_calibration_n` `research.routing.target_risk` | honored | honored | honored | honored | `research/review_routing.py` |
| `retrieval.mode` `retrieval.vector_candidates` `retrieval.lexical_candidates` `retrieval.rrf_k` `retrieval.exclude_degraded` | honored | honored | honored | honored | `research/retrieval_settings.py` |
| `retrieval.matryoshka_enabled` `retrieval.matryoshka_dim` `retrieval.candidate_multiplier` | honored (drives the PG matryoshka query arm) | honored (research layer reads them; the duck query arm exact-scans full width) | pinned-designed-delta | honored-both-query-arm-pg-optimization-only | `handlers/query_search_pg.py` |
| `retrieval.exact_filter_selectivity_threshold` | honored (PG query arm + research layer) | honored (research layer; no HNSW probe to fall back from) | pinned-designed-delta | postgres-query-arm-plus-research-both | `handlers/query_search_pg.py` |
| `retrieval.hnsw.m` `retrieval.hnsw.ef_construction` `retrieval.hnsw.iterative_scan` `retrieval.hnsw.ef_search` `retrieval.hnsw.max_scan_tuples` | honored | typed refusal at load when the raw file tunes a key off the shipped default | refused-typed | refused-under-duckdb-when-set | `backend_gates.py` |
| `retrieval.multi_query.enabled` `retrieval.multi_query.variants` `retrieval.multi_query.timeout_seconds` `retrieval.multi_query.model` `retrieval.multi_query.base_url` | honored | honored | honored | honored | `research/multi_query_settings.py` |
| `routing.prototypes.graph` `routing.prototypes.relational` `routing.prototypes.semantic` | honored | honored | honored | honored | `handlers/router_handler.py` |
| `server.port` | honored | honored | honored | honored | `server_port.py` |
| `server.openai_compat` | honored | honored | honored | honored | `api/http.py` |
| `server.socket_path` `server.socket_port` | honored | honored | honored | honored | `api/socket_target.py` |
| `statements.enabled` `statements.templates_dir` | honored | honored | honored | honored | `tools/pipeline/atomize.py` |
| `statements.max_per_chunk` | honored | honored | honored | honored | `statements/atomize.py` |
| `telemetry.enabled` `telemetry.log_dir` | honored | honored | honored | honored | `telemetry/settings.py` |
| `textbook.min_pages` `textbook.window_pages` `textbook.parser` `textbook.cache_dir` `textbook.window_subprocess` `textbook.window_timeout_s` `textbook.cache_max_mb` `textbook.worker_reuse` `textbook.worker_max_windows` `textbook.worker_idle_ttl_s` `textbook.structured_output` `textbook.citations` | honored | honored | honored | honored | `ingestion/strategies/textbook_settings.py` |
| `topics.enabled` `topics.method` `topics.min_topic_size` `topics.llm` `topics.llm_base_url` `topics.llm_reduction` `topics.fixture_dir` `topics.live_fallback` `topics.zeroshot_min_similarity` | honored | honored | honored | honored | `topics/settings.py` |
| `ui.spa.max_upload_mb` `ui.spa.poll_ms` `ui.spa.request_timeout_s` | honored | honored | honored | honored | `ui/spa.py` |
| `web.user_agent` `web.timeout_seconds` `web.max_bytes` `web.respect_robots` `web.max_pages` `web.max_depth` `web.delay_seconds` `web.allow_private_addresses` | honored | honored | honored | honored | `web/settings.py` |
| `research.release.profiles.exploratory.alpha_reliable` `research.release.profiles.exploratory.alpha_tentative` `research.release.profiles.exploratory.min_audit_sample` `research.release.profiles.exploratory.min_stratum_size` `research.release.profiles.exploratory.partition_rule` `research.release.profiles.exploratory.required_gates` `research.release.profiles.high-assurance.agreement_routing_threshold` `research.release.profiles.high-assurance.alpha_reliable` `research.release.profiles.high-assurance.alpha_tentative` `research.release.profiles.high-assurance.alt_test_margin` `research.release.profiles.high-assurance.alt_test_passing_fraction` `research.release.profiles.high-assurance.content_stability_floor` `research.release.profiles.high-assurance.max_stratum_disparity` `research.release.profiles.high-assurance.min_audit_sample` `research.release.profiles.high-assurance.min_stratum_size` `research.release.profiles.high-assurance.partition_rule` `research.release.profiles.high-assurance.per_code_f1_floor` `research.release.profiles.high-assurance.prompt_stability_alpha` `research.release.profiles.high-assurance.required_gates` `research.release.profiles.team-codebook.agreement_routing_threshold` `research.release.profiles.team-codebook.alpha_reliable` `research.release.profiles.team-codebook.alpha_tentative` `research.release.profiles.team-codebook.alt_test_margin` `research.release.profiles.team-codebook.alt_test_passing_fraction` `research.release.profiles.team-codebook.content_stability_floor` `research.release.profiles.team-codebook.max_stratum_disparity` `research.release.profiles.team-codebook.min_audit_sample` `research.release.profiles.team-codebook.min_stratum_size` `research.release.profiles.team-codebook.partition_rule` `research.release.profiles.team-codebook.per_code_f1_floor` `research.release.profiles.team-codebook.prompt_stability_alpha` `research.release.profiles.team-codebook.required_gates` | honored | honored | honored | honored | `research/release/profiles.py` |

### 1.1 Removed keys (removal ledger)

Removed 2026-11-24 in PR-7 (#579), under plan rulings R5 and R7. The
rule: an honored-by-absence key beats a validated-but-inert key. Each
removal also shrank `config.py` and both shipped YAML files in the same
PR (config lockstep, rule 7).

| Removed key | Why it was dead | Evidence at removal |
|---|---|---|
| `retrieval.vector_index` (G21) | Its only reader was the validator enum. DuckDB hard-codes the exact scan; Postgres keeps pgvector; doctor never read it. | `storage/duckdb_search.py` header; PR-7 |
| `retrieval.fts_rebuild_debounce_s` (G22) | No runtime reader. The debounce the config comments described does not exist; freshness is a row-count watermark (`storage/duckdb_fts.py`, `ensure_fts_index`). | PR-7 |
| `storage.vector_type` (G23) | Validated enum only; the column type is schema-derived (`storage/dialect.py`). At the 2560 width, `halfvec` is required, so the key was a trap. | PR-7 |
| `retrieval.qa_context_in_fts` (G06) | A typed reader with zero consumers on both backends. The migration-023 Postgres index stays in place; it is harmless and pinned as structural. | PR-7 |
| `server.transport` + `CORPUS_KB_TRANSPORT` (G29) | No runtime reader ever existed; transport selection is a CLI flag (`server_wiring.py`). The grader refuted the "honored-both" classification; see section 3.2. | PR-7 |

The legacy alias `search.self_query` is NOT removed: it stays an
accepted alias for `retrieval.self_query` (`config_aliases.py:38`).

### 1.2 The self_query flip (PR-7, R6)

`retrieval.self_query.enabled` gates SelfQueryParser construction in both
wiring twins. Before PR-7 the wirings read only the legacy
`search.self_query.enabled` spelling, so the canonical key defaulted on
while gating nothing. PR-7 wired the canonical key and flipped the
shipped default to `false` (`config.py:565`), so default installs keep
the old behavior: no parser, no extra LLM call. An install that sets the
canonical key to `true` now gets the feature.

### 1.3 The hnsw refusal (PR-6, G16-G20)

The five `retrieval.hnsw.*` keys tune the pgvector HNSW index. Under
DuckDB there is no such index. When the raw user file tunes any of the
five keys off the shipped default and the engine is DuckDB, load fails
with:

> `retrieval.hnsw.* is pgvector-only. Remove the block, or set database.backend to postgres.`

Shipped defaults never trip the refusal: the rule reads the raw file
tree, so write-back files that render defaults load cleanly
(`backend_gates.py:44-62,155-159`).

### 1.4 The graph-backend refusals (PR-6, G09 + mirror)

Both directions refuse at load:

- `graph.backend: postgres` under the DuckDB engine (an explicit file
  choice; the automatic follow-flip never trips):
  > `graph.backend: postgres cannot run on the DuckDB engine. Remove graph.backend, or set database.backend to postgres.`
- `graph.duckdb_graph_backend` tuned off its default under Postgres:
  > `graph.duckdb_graph_backend is a DuckDB setting. Remove it, or set database.backend to duckdb.`

Evidence: `backend_gates.py:50-53,160-167`.

### 1.5 Embedding-width parameterization (PR-5, G28)

The migration DDL carries the `__EMBEDDING_DIMS__` placeholder where the
width belongs. Both migration runners substitute it from
`embedding.dimensions` before they apply a file (`_setup/store_width.py:47`,
`_setup/migrate.py:111,198`, `_setup/migrate_postgres.py:123`), and each
run records the applied width in the store meta table
(`EMBEDDING_WIDTH_KEY`, migration pg 047 / duckdb 048). `CORPUS_KB_EMBEDDING_DIMENSIONS`
flows through `embedding.dimensions`, so the env var now drives
migrate-time DDL on both engines.

The boot guard re-checks the recorded width at startup
(`_setup/version_guard.py:206-233`). A config width that does not match
the store width raises the SAME typed message on both engines
(`config_gates.py:47-60`):

> `Embedding dimensions {config_width} do not match this store's width {store_width}. Re-migrate the store with the new width, or set embedding.dimensions back to {store_width}.`

A store migrated before PR-5 has no meta row; the guard backfills the
live column width (or the legacy 2560 default) on first boot, so every
later boot compares recorded numbers.

### 1.6 The topics lockstep completion (PR-7, G24)

`topics.zeroshot_min_similarity` was readable at runtime but absent from
the defaults inventory, so a setup write-back dropped a user-set value.
PR-7 added the key to all three places in one PR
(`topics/settings.py:50-78`, `get_default_config()`, packaged
`config.yaml`) and rewrote the stale comment that explained the absence.

### 1.7 The silent-ignore-one pins (PR-10, R8)

Four key groups are honored on both backends, with one backend applying
them in a narrower way. They are pins, not bugs; the rationale is the
plan's R8 ruling:

- **G10-G12** (`retrieval.matryoshka_enabled`, `retrieval.matryoshka_dim`,
  `retrieval.candidate_multiplier`): the DuckDB query arm exact-scans the
  full-width column (`storage/duckdb_search.py:11-13`), which is the
  exact stage of the matryoshka two-tier without the approximate probe.
  Postgres runs the sliced probe plus exact re-rank. Results are at
  least as accurate on DuckDB; only latency differs. The research layer
  reads the keys on both backends (`research/retrieval_settings.py:21-28,72-76`,
  `storage/dialect.py:102-110`), so refusing them under DuckDB would be
  wrong.
- **G13** (`retrieval.exact_filter_selectivity_threshold`): meaningless
  on the DuckDB query surface, because DuckDB has no HNSW probe to fall
  back from — it always exact-scans. Postgres applies it on its query
  arm; the research layer applies it on both backends.
- **G14** (`ingestion.concurrency`): a gateway answers 1 worker by
  design — DuckDB is a single-writer file engine
  (`tools/ingest_common.py:217-232`). Postgres runs the configured
  bound.

---

## 2. Operation matrix

70 operations, in declaration order. The universe is the parity
catalogue (`PARITY_OPS`) plus the HTTP-only `ingest_upload`. Set
equality with `ops_backend_matrix.py` is pinned in both directions by
`tests/test_config_backend_parity_gate.py::test_ops_matrix_covers_parity_catalogue`,
and the live-matrix script pins the same set (`scripts/duck_live_matrix.py`).

Proof column: PR-8 (#585) added the DuckDB parity legs that drive each
operation against a real DuckDB file, through the shared handlers. One
operation, one handler, many surfaces; the envelope keys match on both
backends.

| Operation | Postgres | DuckDB | Proof |
|---|---|---|---|
| `topics_list` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `labeling_list` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `labeling_lfs` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `classification_list` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `analysis_select_method` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `statements_list` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `statements_timeline` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `conflicts_list` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `beliefs_current` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `queue_enqueue` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `queue_status` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `ingest_status` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `list_documents` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `get_document_tags` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `get_metadata` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `release_assess` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `notebook_evidence` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `notebook_uncoded` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `notebook_overlap` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `delete_document` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `project_provision` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `project_list` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `project_drop` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `coding_status` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `coding_export` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `events_summary` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `keyphrases_extract` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `lexical_classify` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `neighbors_classify` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `centroids_classify` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `clusters_inspect` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `duplicates_screen` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `outliers_rank` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `sentiment_screen` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `active_learn_next` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `nli_classify` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `contradictions_screen` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `survey_openend_codes` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `usability_severity` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `emotion_tag` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `labeling_build` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `classification_build` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `topics_build` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `topics_trends` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `analysis_backfill_dates` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `analysis_embed_meta` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `analyze_run` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `conflicts_scan` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `labeling_triage` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `labeling_gold_check` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `queue_cancel` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `research_cycle` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `research_demo` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `demo_load` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `extraction_tune` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `ingest_url` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `release_compare` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `ingest_reingest` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `ingest_directory` | honored | honored | PR-8 parity leg over `PARITY_OPS` |
| `query` | honored | honored (exact-scan + BM25 FTS arms) | PR-8 parity leg + section 4 rows 1-3 |
| `search_context` | honored | honored (exact-scan + BM25 FTS arms) | PR-8 parity leg + section 4 rows 1-3 |
| `verify_answer` | honored | honored (exact-scan + BM25 FTS arms) | PR-8 parity leg + section 4 rows 1-3 |
| `notebook_ask` | honored | honored (exact-scan + BM25 FTS arms) | PR-8 parity leg + section 4 rows 1-3 |
| `add_entity` | honored (RLS enforced) | honored (no RLS; single user) | PR-8 parity leg + section 4 row 4 |
| `add_relation` | honored (RLS enforced) | honored (no RLS; single user) | PR-8 parity leg + section 4 row 4 |
| `search_graph` | honored (RLS enforced) | honored (no RLS; single user) | PR-8 parity leg + section 4 row 4 |
| `get_entity_relations` | honored (RLS enforced) | honored (no RLS; single user) | PR-8 parity leg + section 4 row 4 |
| `graph_neighbors` | honored (RLS enforced) | honored (no RLS; single user) | PR-8 parity leg + section 4 row 4 |
| `graph_traverse` | honored (RLS enforced) | honored (no RLS; single user) | PR-8 parity leg + section 4 row 4 |
| `ingest_upload` | honored | honored | PR-8 HTTP multipart upload leg |

The `query`, `search_context`, `verify_answer`, and `notebook_ask` rows
carry the search-arm designed deltas (section 4, rows 1-3). The graph
rows carry the row-level-security delta (section 4, row 4). Every
envelope stays identical; only the physical plan differs.

---

## 3. The 29-gap ledger

The synthesis ranked 29 gaps (G01-G29). Every gap is closed: fixed,
removed, refuted, or pinned with a stated reason. Classes are the final
post-program state.

| ID | Gap (one line) | Fix PR | Final class |
|---|---|---|---|
| G01 | Analysis date ops ran Postgres-only SQL under DuckDB; engine-shaped crash | PR-1 (#572) | fixed — engine dispatch, Duck SQL twin, no `SET LOCAL` |
| G02 | add_entity/add_relation CLI writes stayed invisible until an unrelated command advanced projections (#530) | PR-3 (#574) | fixed — read models advance in the same command |
| G03 | research_ingest of an ingested source FK-failed into the DLQ (#529) | PR-2 (#573) | fixed — idempotent re-ingest |
| G04 | DuckDB mirror of meta_embeddings omitted the Postgres CHECKs | PR-4 (#575) | fixed — CHECKs mirrored (duckdb 047), allowlist ratcheted down |
| G05 | `retrieval.self_query.enabled` defaulted on while gating nothing | PR-7 (#579) | fixed — wired + default flipped false (section 1.2) |
| G06 | `retrieval.qa_context_in_fts` had a typed reader and zero consumers | PR-7 (#579) | removed (section 1.1) |
| G07 | migrate flags silently dropped under DuckDB | PR-4 (#575) | fixed — typed refusals |
| G08 | `queue.autorun` wired on the Postgres path only | PR-10 (this PR) | refuted at HEAD — honored on both (section 3.1) |
| G09 | `graph.backend: postgres` under DuckDB built silently | PR-6 (#577) | fixed — typed refusal (section 1.4) |
| G10 | `retrieval.matryoshka_enabled` unread by the DuckDB search arm | PR-10 (this PR) | pinned-designed-delta (section 1.7) |
| G11 | `retrieval.matryoshka_dim` same arm | PR-10 (this PR) | pinned-designed-delta (section 1.7) |
| G12 | `retrieval.candidate_multiplier` same arm | PR-10 (this PR) | pinned-designed-delta (section 1.7) |
| G13 | `retrieval.exact_filter_selectivity_threshold` inert on the DuckDB query surface | PR-10 (this PR) | pinned-designed-delta (section 1.7) |
| G14 | `ingestion.concurrency` hard 1 under DuckDB | PR-10 (this PR) | pinned-designed-delta (section 1.7) |
| G15 | setup `--build-local` / `--skip-image-verify` silently ignored under `--backend duckdb` | PR-4 (#575) | fixed — typed refusals |
| G16 | `retrieval.hnsw.m` silent no-op under DuckDB | PR-6 (#577) | fixed — typed block refusal (section 1.3) |
| G17 | `retrieval.hnsw.ef_construction` same | PR-6 (#577) | fixed — same refusal |
| G18 | `retrieval.hnsw.iterative_scan` same | PR-6 (#577) | fixed — same refusal |
| G19 | `retrieval.hnsw.ef_search` same | PR-6 (#577) | fixed — same refusal |
| G20 | `retrieval.hnsw.max_scan_tuples` same | PR-6 (#577) | fixed — same refusal |
| G21 | `retrieval.vector_index` read by nothing | PR-7 (#579) | removed (section 1.1) |
| G22 | `retrieval.fts_rebuild_debounce_s` read by nothing | PR-7 (#579) | removed (section 1.1) |
| G23 | `storage.vector_type` changed nothing | PR-7 (#579) | removed (section 1.1) |
| G24 | `topics.zeroshot_min_similarity` dropped by setup write-back | PR-7 (#579) | fixed — three-way lockstep (section 1.6) |
| G25 | Ops with no DuckDB CI leg | PR-8 (#585) | fixed — parity legs + upload leg (section 2) |
| G26 | Masked parity legs proved only guard envelopes | PR-8 (#585) | fixed — legs run the op bodies on DuckDB |
| G27 | Documented designed deltas + #527 P3s | PR-4 (#575) + PR-10 (this PR) | fixed (P3 subset) + documented (section 4) |
| G28 | Non-2560 width accepted at setup, refused only at first write (#559) | PR-5 (#576) | fixed — parameterized width + boot guard (section 1.5) |
| G29 | `server.transport` + `CORPUS_KB_TRANSPORT` | PR-7 (#579) | removed — grader-refuted dead key (section 3.2) |

### 3.1 G08: refuted at HEAD (R2)

The synthesis claimed `queue.autorun` wires on the Postgres path only.
That claim is false at the merged state. Evidence chain, all verified at
HEAD:

1. `startup` dispatches the DuckDB startup twin at
   `server_wiring.py:359-361`.
2. `run_all` (`server_wiring.py:400`) is the SHARED serve loop for both
   backends; every serve path calls it after `startup`
   (`server_wiring.py:561-563`).
3. `run_all` calls `start_queue_autorun(services["pool"] or
   services.get("gateway"), config)` at `server_wiring.py:434-436` — on
   DuckDB that argument is the gateway.
4. `QueueWorker.__init__` takes `asyncpg.Pool | SqlGateway`
   (`queue/worker.py:82-90`), and `require_queue_backend` accepts
   `("postgres", "duckdb")` (`queue/worker_support.py:80-95`).
5. The stale "Postgres only" wording in the `queue/autorun.py` docstring
   was corrected in PR-6; the docstring now says "Runs on both engines".

No fix PR was needed. The matrix declares `queue.autorun` honored on
both backends, and the gate holds that declaration against the reader.

### 3.2 G29: born from the grader's refutation

The config matrix classified `server.transport` as honored-both. The
grader refuted that verdict (42 of 43 confirmed; this was the one
refusal): no runtime reader exists, transport selection is argparse-only
in `server_wiring.py`, and the config default contradicted the CLI
default — proof the key never worked. G29 was added to the ledger as a
dead key on both backends, and PR-7 removed the key and the env var in
one lockstep (plan ruling R5). `doctor` warns on any set-but-unmapped
`CORPUS_KB_*` variable, so a leftover `CORPUS_KB_TRANSPORT` gets an
honest signal.

---

## 4. Designed deltas (the G27 tail)

These differences exist because the engines are different. Each one is
documented, pinned by tests, and visible in response envelopes where a
client can see it.

| # | Delta | Postgres | DuckDB | Why it stays | Evidence |
|---|---|---|---|---|---|
| 1 | Search index modes | pgvector HNSW approximate probe; `tsvector` + GIN lexical | `exact_scan` vector arm; `fts_bm25` lexical arm | DuckDB ships no HNSW in this regime; the exact scan is engine-honest | `storage/duckdb_search.py:11-13`; `vector_index_mode` / `lexical_index_mode` envelope keys |
| 2 | Lexical arm shape | `tsquery` with idempotent sanitization | Disjunctive-only `match_bm25` 1.4 scalar macro, param-bound | Conjunctive plus stopword zeroes out (duckdb-fts #15, present in 1.4.5) | `storage/duckdb_search.py:19-24` |
| 3 | Zero-vector sentinel ordering | Zero norms order last via the degraded filter | A `-2.0` similarity sentinel, deterministic id order | `array_cosine_distance` returns a finite 2.0 for zero vectors, not NaN | `storage/duckdb_search.py:11-16`; tests pin both orders |
| 4 | Row-level security | RLS policies on every tenanted table | No RLS; single-writer single-user file | DuckDB has no policy engine; tenant predicates still apply | `storage/tenant_conn.py`; section 2 graph rows |
| 5 | Doctor duck-probe typing | Probes return typed check results | Same: typed status/detail/fix, never engine-shaped exceptions | A probe failure must print a named fix, not a stack trace | `_setup/doctor_duckdb.py:159-201` (the PR-4-dropped item, documented here) |
| 6 | Matryoshka arm | Two-tier approximate probe + exact re-rank | Full-width exact scan (the exact stage only) | Results at least as accurate; latency differs (section 1.7) | `storage/dialect.py:102-110`; `handlers/query_search_pg.py` |

G27's other half (the #527 P3 subset: the Postgres BFS depth-cap gap
versus AGE, and the inductive-store null-masking) was fixed in PR-4
(#575), so its rows do not appear here.

---

## Provenance

- Plan: `.zcode/agent-tasks/parity-recovered-plan.md` in the repo (the
  PR-10 section; rulings R2, R5-R8; the CI rules; the G01-G29 gap table).
- Spec: `.zcode/duckdb-parity-spec-2026-11-22.md` (approved program
  spec, Done-when audit).
- User-facing summary: [STORAGE-BACKENDS.md](../STORAGE-BACKENDS.md)
  (must match this matrix; PR-10 cross-checked each row).
