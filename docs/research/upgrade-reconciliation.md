# Upgrade reconciliation — U1 through U48 (Step 0, P0)

One row per upgrade from `corpus-kb-implementation-spec-v8.md` §2–3 and
`corpus_kb_execution_spec_v6.md` §4, verified against the repo at
`feature/kb-u48-p0-reconciliation` (basis `d0a5568`). Evidence is
`file:symbol`. Where the spec hypotheses and the repo disagreed, the repo
won and the row says so.

Statuses: `done` (fully covered — do not rebuild), `partial` (extension
point exists, remaining work named), `absent` (nothing in the repo yet).
"Remaining work" names the NEXT phase (P1–P5) that owns it.

| U# | item | status | evidence (file:symbol) | remaining work |
|----|------|--------|------------------------|----------------|
| U1 | Release lifecycle events, projector, four tables | absent | no `codebook_release*` tables/events (`grep codebook_release` = 0 hits); nearest building blocks: `src/corpus_kb/domain/codebook.py:CodebookVersion` aggregate, `migrations/014_codebook_versions_unique.sql` uniqueness, `projections/research/code_projector.py` | P1: migration + events + projector (v6 §6 table/event list) |
| U2 | Hashed release manifest | absent | `grep ReleaseManifest` = 0 hits | P1: `ReleaseManifest` + canonical JSON + SHA-256 |
| U3 | Profiles exploratory/team-codebook/high-assurance; enforcement off\|warn\|enforce | absent | `grep "high_assurance|exploratory"` = 0 hits in `src/corpus_kb/` | P1: `research.release.*` config block (both YAMLs) + resolver |
| U4 | `assess` evidence bundle composing existing gates | partial | `research/cycle_gates.py:DEFAULT_HALT_ON` + `effective_halt_on` compose halt gates only (halt-only, no assess bundle); `research/cycle_reads.py` read helpers exist | P1: compose cycle+IRR+exhaustiveness+calibration+quote/stability evidence into one `assess` result |
| U5 | Commands assess/review-release/release/status/compare on CLI/MCP/HTTP | absent | `surface_registry.py:SURFACES` has none of them; `cli.py` registers research/coding/codebook/review groups only | P1: handlers + surface rows + parity test (`surface_registry_parity.assert_full_surface_parity` is ready for the P5 enforcement flip) |
| U6 | Harness UX: review packet, slash-command/skill templates, next-action hints | partial | `surface_registry.py:WRAPPERS` + `scripts/gen_harness_pack.py` generate the command pack; `research/guide_copy.py` carries guided copy; no review-release packet renderer | P1/P5: review packet (JSON + markdown via `cycle_render.py`) |
| U7 | Code-definition completeness gate | partial | `domain/codebook.py:CodebookVersion.add_codes` requires name+definition; `refine_definition` holds inclusion/exclusion; exemplars via `add_exemplar_texts` (text_sha256 refs) — data model complete, no GATE | P2: completeness gate over the aggregate fields |
| U8 | Unitization agreement gate | partial | `coding/coder_window.py` (window boundary units) + `coding/quote_verification.py` (span verification, exact/normalized_match/not_found) — logic exists, no agreement GATE | P2: boundary-agreement gate reusing span logic |
| U9 | Partition independence/leakage gate | absent | `partitioning.py` is the unstructured partition wrapper (doc chunking), no discovery/calibration/test/audit disjointness anywhere | P2: partition disjointness gate |
| U10 | Reproducibility completeness gate | absent | no manifest (see U2); `research/run_inputs.py` records run inputs only | P1 (after U2): machine-profile completeness gate |
| U11 | Held-out per-code precision/recall/F1, per stratum | partial | `coding/floors_calibration.py:calibrate_code_floors` (per-code floors on held-out data); no per-stratum breakdown | P2: stratum split + P/R/F1 reporting (v6 §7 U11) |
| U12 | Prompt-stability gate | absent | `grep prompt_stability` = 0 hits; `coding/reliability.py` covers label reliability only | P3: stored prompt variants + alpha gate |
| U13 | Alternative Annotator Test gate | absent | `grep "alt_test|Alternative Annotator"` = 0 hits | P3: new `coding/alt_test.py` (read Calderon et al. first) |
| U14 | Debiased (prediction-powered) estimates | absent | `grep "prediction.powered|ppi"` = no estimator | P3: small numpy PPI estimator + raw/debiased dual reporting |
| U15 | Bias audit by stratum | absent | `research/g3_audit.py` has no stratum logic (`grep stratum` = 0 hits in file) | P3: per-stratum confusion + disparity flag |
| U16 | Topic-content stability (top-term Jaccard) | partial | `research/cluster_stability.py:ari` + `mean_pairwise_ari` (membership stability only); `coding/keyness.py` computes top terms | P3: add top-term Jaccard beside ARI; gate separately |
| U17 | Stratified audit sampling | partial | `coding/reliability_sampling.py` samples reliability checks; no strata/inclusion-probability/seed design | P3: seeded stratified design (code × agreement band) |
| U18 | Run-to-run agreement score as uncertainty signal | partial | `coding/uncertainty/` tiers 0–3 (`tier3_consistency.py` self-consistency) + `coding/confidence_routing.py` router; multi-run agreement exists, calibration-vs-human validation does not | P3: ECE/AUROC validation before it may gate |
| U19 | Agreement-routed human review via gray-zone halt queue | partial | `research/cycle_gates.py` gray_zone_escalation halt + `coding/uncertainty/review_priority.py` routing; `research/review_surface.py` renders the review queue | P3: wire agreement score into queue ordering with reason |
| U20 | `hnsw.iterative_scan` as transaction-local setting | done | `research/search_settings.py:HnswSettings` + `load_hnsw_settings` + `apply_hnsw_settings` (fail-closed gate `supports_iterative_scan`); emitted in `handlers/query_handler.py:handle_search` and `research/retrieval.py:research_search` inside the tenant transaction; config `search.hnsw.*` in `config.py:get_default_config` + both YAMLs | — (P3 may add a no-leak-across-tenants integration test on live PG) |
| U21 | Exact-scan fallback for highly selective filters | absent | `grep "exact_filter|seq_scan"` = only `analytics_sql.py` governance `SET LOCAL enable_seqscan` (different purpose) | P3/P4: selectivity threshold + filtered exact search |
| U22 | Benchmark: halfvec, 256-d probe, scan modes, binary+rerank | absent | `scripts/bench_setup.py:main` is a declared placeholder | P3: benchmark harness under `docs/research/benchmarks/` |
| U23 | pg_textsearch BM25 + RRF benchmark | absent | `grep pg_textsearch` = 0 hits (also AGPL-check: NOT adopted, per license rule native FTS stays) | P3: benchmark-only, adopt only if it wins |
| U24 | Deduction guard: warn/enforce/--allow-draft sandbox | absent | `research/deductive_run.py` has no release/enforcement logic | P5 (v6 ordering): guard after release layer exists |
| U25 | Monitoring creates change proposals, never mutates releases | absent | `research/cycle.py` has no ChangeProposed events | P5 (after U1) |
| U26 | License hygiene | done | `scripts/generate_license_lock.py` (regenerates from importlib.metadata) + `.github/workflows/license-check.yml:license-check` (`--check` gate) + committed `licenses.lock.json` (145 packages). No GPL/AGPL packages found. Flagged weak-copyleft (pre-existing, accepted): psycopg/psycopg2-binary/psycopg-pool LGPL, certifi+tqdm MPL-2.0, dirtyjson AFL-3.0 | — (keep lock in lockstep when deps change) |
| U27 | REFI-QDA export/import vs official XSD | absent | `grep REFI` = 0 hits | P6 |
| U28 | MAPIE conformal cross-check in tests only | absent | `grep MAPIE` = 0 hits (repo has own conformal code) | P6 |
| U29 | Inductive baselines (GATOS, TnT-LLM, LLooM) | absent | `grep "GATOS|TnT|LLooM"` = 0 hits | P6 |
| U30 | Optional cross-encoder rerank stage over hybrid candidates | partial | `rag/reranker.py:PgmlReranker` (pgml.rank, wired) and `OllamaReranker` (parses `float(raw.strip())` at reranker.py:163 — wrong contract for qwen3-reranker's yes/no token format); `build_reranker` (reranker.py:200) has NO production call site (only `create_reranker` is wired: `handlers/query_handler.py:QueryHandler.__init__`, `server_wiring.py`) | P3: fix the qwen3 parsing contract + provider-config gate + NDCG gate on benchmark |
| U31 | Embedder upgrade benchmark (EmbeddingGemma 2, Qwen3, current) | absent | `scripts/bench_setup.py` placeholder; embedder profiles only in installer defaults | P3: U22 harness extension |
| U32 | Matryoshka two-stage search (256-d probe, full-dim rerank) | partial | SPEC HYPOTHESIS ADJUSTED: two-stage search EXISTS on the main path — `handlers/query_handler.py:handle_search` matryoshka arm (1024-d candidate CTE + full-dim re-rank), `rag/embedder.py:OllamaEmbedder.embed_matryoshka` (front-slice + renormalize), `migrations/010_matryoshka_1024.sql` index — config-OFF default (`search.matryoshka_enabled: false`); 256-d probe + MRL-model gating + benchmark absent | P3: 256-d probe column (migration 016 `embedding_256` exists on the research grain), benchmark before enabling |
| U33 | Dawid-Skene aggregation to adjudicated reference labels | absent | `grep Dawid` = 0 hits | P3: ~50-line numpy EM |
| U34 | Coder-panel diversity / judge-bias guard | absent | no model-family recording (`grep family` = only g3_audit text) | P3 |
| U35 | Judgment distribution (mean over sampled outputs) | partial | `coding/uncertainty/tier1_logprob.py` reads token logprobs where exposed; `coder_client.py` greedy-only decoding | P3: distribution mean behind config + doctor verification |
| U36 | Saturation curve reporting | partial | `coding/saturation.py:isr` (ISR = Incremental Sampling Rate) + `run_stop` rule; `coding/saturation_query.py:compute_saturation` computes the DB-side values; no cumulative curve rendering | P3: curve (cumulative unique/total by unit index) in reports |
| U37 | Active review batching (uncertainty + diversity) | absent | no `review_batch.py` | P4 |
| U38 | Weak-supervision aggregation (conditional) | absent | no aggregation module (see U33) | P4 (conditional, ≥3 signals) |
| U39 | Label-error triage without Cleanlab | absent | no out-of-sample label queue | P4 |
| U40 | pgvector relaxed-ordering correctness fix | done | U40 pattern (MATERIALIZED candidate CTE, exact outer `ORDER BY score + 0 DESC`) applied to every filtered vector KNN: `handlers/query_handler.py:handle_search` (pgml, ollama-plain, matryoshka arms) + `handle_search_similar`, `research/retrieval.py:_unit_arms` + `_exchange_arms`; regression tests `tests/test_vector_search_ordering.py`; doctor gate `_setup/doctor_research.py:check_pgvector_version`. `research/analytics_sql.py` KNN reads (governance analytics over fixed-shaped candidate sets) intentionally deferred to P3 (different access pattern, no dynamic filters) | P3: analytics_sql sweep + live-PG monotonic-distance test |
| U41 | Risk–coverage thresholding | absent | no risk-coverage code | P4 (needs U47) |
| U42 | Provenance-keyed exact result cache | absent | `handlers/idempotency.py` is command dedup, not LLM result caching | P3 |
| U43 | Structured-output canary + validate/repair | absent | `grep canary` = 0 hits in src (P0 adds the EMBEDDING canary, not the structured-output one) | P1: startup/doctor canary + validate/repair + not_enforced gating |
| U44 | Deterministic clustering inputs | partial | `coding/inductive_cluster.py` accepts seeds (`random_state`/seed plumbing in `coding/_inductive_types.py`); no persisted embedding-matrix hash, no seed sweep in `research/cluster_stability.py` | P1: manifest recording + ≥10-seed sweep |
| U45 | Hybrid lexical+vector retrieval with RRF — evaluate and wire | partial | SPEC HYPOTHESIS ADJUSTED: there is NO `HybridSearcher` class (AGENTS.md is stale on this). Hybrid search is SQL-side: `migrations/006_rrf_fusion.sql:corpus.rrf_fusion` (Σ1/(k+rank), k=60) orchestrated by `handlers/query_handler.py:handle_search` (vector arm + contextual FTS arm + RRF) and `research/retrieval.py:research_search` (two-level RRF, `RRF_K=60`, native FTS `ts_rank`); eval harness absent | P3: `research/retrieval_eval.py` (recall@k/MRR/nDCG@10/p95) on human-reviewed labels before any default flip |
| U46 | halfvec benchmark + optional adoption | partial | halfvec IN the research grain: `migrations/016_research_domain.sql` HNSW on `(embedding::halfvec(1024)) halfvec_cosine_ops` + `research/retrieval.py` halfvec probes + `research/analytics_sql.py`; main-path benchmark absent (`scripts/bench_setup.py` placeholder); main path still full `vector(4096)` | P3: U22 benchmark (recall/size/build/p95) before any main-path adoption |
| U47 | Confidence-signal benchmark | partial | signals exist (`coding/uncertainty/tier0_similarity.py`, `tier1_logprob.py`, `tier2_hedging.py`, `tier3_consistency.py`, `review_priority.py` router); no AUROC/ECE/Brier/risk-coverage calibration harness, no calibrator | P4: calibration split + evaluate-once discipline |
| U48 | Evidence-quote verification + multi-run consensus | partial | quote verification DONE: `coding/quote_verification.py` (exact/normalized_match/not_found, attribution firewall, `diffflib` fuzzy stage); multi-run consensus (N-run clustering by embedding similarity, per-theme consistency) ABSENT; reviewer rubric events ABSENT | P1: N-run consensus + rubric in `research/review_surface.py` |

## Tallies (verified 2026-10-09, this branch)

- **done = 3** — U20, U26, U40
- **partial = 18** — U4, U6, U7, U8, U11, U16, U17, U18, U19, U30, U32,
  U35, U36, U44, U45, U46, U47, U48
- **absent = 27** — U1, U2, U3, U5, U9, U10, U12, U13, U14, U15, U21, U22,
  U23, U24, U25, U27, U28, U29, U31, U33, U34, U37, U38, U39, U41, U42, U43

3 + 18 + 27 = 48 rows.

## Spec-vs-repo discrepancies found in Step 0 (repo won)

1. **No `HybridSearcher` class** (AGENTS.md and the U45 hypothesis both name
   it): hybrid search is the SQL function `corpus.rrf_fusion` (migration
   006) orchestrated inline in the query handler and the research retrieval
   module.
2. **U32 two-stage search exists** (config-off) on the main path — the
   "no two-stage search" hypothesis was wrong; what is missing is the
   256-d probe, MRL gating, and the benchmark.
3. **schema.sql `vector(4096)` is load-bearing**, not documentation:
   `tests/research_db.py` executes the file verbatim, migration 012 copies
   the typmod (`vector(4096)` for `answer_ctx_vectors`), and the
   event-store tests insert 4096-dim vectors. The P0 brief's suggestion to
   retyp to `vector(1024)` would break all three; the column is instead
   documented as the deliberate max-capacity slot and pinned by
   `tests/test_schema_reference.py`.
4. **`search.hnsw_ef_search` (100) was an inert config knob documented as
   live** (`docs/INGESTION.md` row, no reader anywhere). Removed and
   replaced by the read `search.hnsw.{iterative_scan,ef_search,
   max_scan_tuples}` block (U20), following the repo precedent of removing
   inert knobs (commit 24d461c).
5. **Licenses**: `licenses.lock.json` + CI gate already existed (U26 done);
   regenerated copies from a drifted local venv would corrupt the
   committed baseline (the committed lock tracks a fresh-install resolve
   and is NEWER than the stale local venv for every drifted package), so
   the lock was verified, not wholesale rewritten. No GPL/AGPL packages;
   weak-copyleft (LGPL/MPL/AFL) entries are flagged above. One UNKNOWN
   entry (tiktoken, whose only metadata is the full MIT license text) was
   hand-verified as MIT and pinned via `LICENSE_OVERRIDES` in
   `scripts/generate_license_lock.py` so every platform resolves it
   deterministically.
