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
| U3 | Profiles exploratory/team-codebook/high-assurance; enforcement off\|warn\|enforce | done | `research/release/profiles.py:resolve_profile` + `RELEASE_CONFIG_DEFAULTS` (config block in `config.py` + both YAMLs); enforcement now read from CONFIG by `research/deduction_guard.py:release_guard_settings` (U24 wiring) | — |
| U4 | `assess` evidence bundle composing existing gates | partial | `research/cycle_gates.py:DEFAULT_HALT_ON` + `effective_halt_on` compose halt gates only (halt-only, no assess bundle); `research/cycle_reads.py` read helpers exist | P1: compose cycle+IRR+exhaustiveness+calibration+quote/stability evidence into one `assess` result |
| U5 | Commands assess/review-release/release/status/compare on CLI/MCP/HTTP | done | ONE shared handler per op (`handlers/release_handlers.py`: `assess_release`/`request_release`/`review_release`/`execute_release`/`status_report`/`compare_version_releases`); all three transports wired — CLI group `corpus-kb release ...` (`cli_release.py`, mounted in `cli.py`), MCP `release_*` tools (`tools/release_tools.py`, names in `validate_configs.VALID_TOOL_NAMES`), HTTP `/api/release/*` (`api/research_http.py`); surface rows in `surface_registry.py` with full cli+mcp+http parity; D1 status payload (stage/state/blockers/literal next command/sufficiency) + D3 honest exit codes + D4 stable payload schemas pinned by `tests/test_surface_parity.py`. DEVIATION (documented): a sixth surface `release-request` was added — no production path opened release drafts before P7, so the five v6 §6 commands were unreachable without it; and `release-execute` records the freshly evaluated gate results on the DRAFT before the transition (same pure gate functions `assess` reports — recording is bookkeeping, not new evidence) | — (P7 landed; see Addendum below) |
| U6 | Harness UX: review packet, slash-command/skill templates, next-action hints | done | review packet: `handlers/release_handlers.py:ReviewPacket` (evidence, failing + not_evaluable gates, waivers, SPECIFIC next actions) rendered by `cli_release.py:_print_review_packet`; slash-command/skill template `corpus-release` GENERATED from `surface_registry.py:WRAPPERS` via `scripts/gen_harness_pack.py` (`.claude/commands/` + `.opencode/command/`, drift-gated); next-action hints are guide_copy literals (`research/guide_copy.py:RELEASE_NEXT_*` — every `release status`/packet `recommended_next_action` is a runnable `corpus-kb release ...` command) | — (P7 landed) |
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
| U17 | Stratified audit sampling | partial | `coding/reliability_sampling.py` samples reliability checks; no stratum/inclusion-probability/seed design | P3: seeded stratified design (code × agreement band) |
| U18 | Run-to-run agreement score as uncertainty signal | partial | `coding/uncertainty/` tiers 0–3 (`tier3_consistency.py` self-consistency) + `coding/confidence_routing.py` router; multi-run agreement exists, calibration-vs-human validation does not | P3: ECE/AUROC validation before it may gate |
| U19 | Agreement-routed human review via gray-zone halt queue | partial | `research/cycle_gates.py` gray_zone_escalation halt + `coding/uncertainty/review_priority.py` routing; `research/review_surface.py` renders the review queue | P3: wire agreement score into queue ordering with reason |
| U20 | `hnsw.iterative_scan` as transaction-local setting | done | `research/search_settings.py:HnswSettings` + `load_hnsw_settings` + `apply_hnsw_settings` (fail-closed gate `supports_iterative_scan`); emitted in `handlers/query_handler.py:handle_search` and `research/retrieval.py:research_search` inside the tenant transaction; config `search.hnsw.*` in `config.py:get_default_config` + both YAMLs | — (P3 may add a no-leak-across-tenants integration test on live PG) |
| U21 | Exact-scan fallback for highly selective filters | absent | `grep "exact_filter|seq_scan"` = only `analytics_sql.py` governance `SET LOCAL enable_seqscan` (different purpose) | P3/P4: selectivity threshold + filtered exact search |
| U22 | Benchmark: halfvec, 256-d probe, scan modes, binary+rerank | absent | `scripts/bench_setup.py:main` is a declared placeholder | P3: benchmark harness under `docs/research/benchmarks/` |
| U23 | pg_textsearch BM25 + RRF benchmark | absent | `grep pg_textsearch` = 0 hits (also AGPL-check: NOT adopted, per license rule native FTS stays) | P3: benchmark-only, adopt only if it wins |
| U24 | Deduction guard: warn/enforce/--allow-draft sandbox | done | `research/deduction_guard.py:decide_guard` + `guard_deductive_run` (reads `research.release.enforcement`/`default_profile` from config via `release_guard_settings`); wired into `research/deductive_run.py:run_deductive` (block = honest `blocked_unreleased_draft` status, no run started; `allow_draft` labels the run `sandbox`) | — (P5/P7: CLI `--allow-draft`/`--no-cache` flags on the five-command surface) |
| U25 | Monitoring creates change proposals, never mutates releases | done | `research/monitoring.py:alarms_from_findings` + `propose_from_alarms` (successor draft via `ReleaseService.propose_change`, alarms in `metric_snapshots.monitoring_alarms`); wired at the cycle stage boundaries (`research/cycle.py:_propose_alarms`); parent state/hash never mutated | — (P7 done: proposals render on the status surface, `handlers/release_handlers.py:status_report:pending_change_proposals`) |
| U26 | License hygiene | done | `scripts/generate_license_lock.py` (regenerates from importlib.metadata) + `.github/workflows/license-check.yml:license-check` (`--check` gate) + committed `licenses.lock.json` (145 packages). No GPL/AGPL packages found. Flagged weak-copyleft (pre-existing, accepted): psycopg/psycopg2-binary/psycopg-pool LGPL, certifi+tqdm MPL-2.0, dirtyjson AFL-3.0 | — (keep lock in lockstep when deps change) |
| U27 | REFI-QDA export/import vs official XSD | absent | `grep REFI` = 0 hits | P6 |
| U28 | MAPIE conformal cross-check in tests only | absent | `grep MAPIE` = 0 hits (repo has own conformal code) | P6 |
| U29 | Inductive baselines (GATOS, TnT-LLM, LLooM) | absent | `grep "GATOS|TnT|LLooM"` = 0 hits | P6 |
| U30 | Optional cross-encoder rerank stage over hybrid candidates | partial | `rag/reranker.py:PgmlReranker` (pgml.rank, wired) and `OllamaReranker` (parses `float(raw.strip())` at reranker.py:163 — wrong contract for qwen3-reranker's yes/no token format); `build_reranker` (reranker.py:200) has NO production call site (only `create_reranker` is wired: `handlers/query_handler.py:QueryHandler.__init__`, `server_wiring.py`) | P3: fix the qwen3 parsing contract + provider-config gate + NDCG gate on benchmark |
| U31 | Embedder upgrade benchmark (EmbeddingGemma 2, Qwen3, current) | absent | `scripts/bench_setup.py` placeholder; embedder profiles only in installer defaults | P3: U22 harness extension |
| U32 | Matryoshka two-stage search (256-d probe, full-dim rerank) | done | SPEC HYPOTHESIS ADJUSTED: two-stage search EXISTS on the main path — `handlers/query_handler.py:handle_search` matryoshka arm (1024-d candidate CTE + full-dim re-rank), `rag/embedder.py:OllamaEmbedder.embed_matryoshka` (front-slice + renormalize), `migrations/010_matryoshka_1024.sql` index — config-OFF default (`search.matryoshka_enabled: false`). MRL gating now code-enforced (issue #81): declared flag `embedding.mrl_trained: false` default (`config.py:get_default_config` + both config.yaml files; qwen3-embedding is MRL-trained, nomic-embed-text is NOT); `config.py:matryoshka_mrl_error` raises from `load_config` on the invalid combination (honest refusal, never a silent activation); `_setup/validate_configs.py:validate_runtime_config` declares the key in RUNTIME_CONFIG_KEYS and reports the same cross-field rule for the shipped YAMLs; tests `tests/test_u32_mrl_gating.py` | P3: 256-d probe column (migration 016 `embedding_256` exists on the research grain), benchmark before enabling |
| U33 | Dawid-Skene aggregation to adjudicated reference labels | absent | `grep Dawid` = 0 hits | P3: ~50-line numpy EM |
| U34 | Coder-panel diversity / judge-bias guard | absent | no model-family recording (`grep family` = only g3_audit text) | P3 |
| U35 | Judgment distribution (mean over sampled outputs) | done | Production call site wired (issue #81): `coding/coder_dispatch.py:dispatch_chunk` routes on the judgment-distribution mean when `research.judgment_distribution.use_mean` is on (explicit arg > config key; the greedy reply stays the decision source and with the key OFF the rows are byte-identical and exactly one model call is made); `coding/judgment_sampling.py:sample_judgments` makes JUDGMENT_MEAN_SAMPLES=4 extra draws at JUDGMENT_SAMPLE_TEMPERATURE=0.7 and folds them through `coding/uncertainty/judgment_distribution.py:judgment_mean`; `coding/judgment_sampling.py:judgment_mean_enabled` is the typed config read (measurement_settings); `coding/coder_client.py:call_ollama(temperature=)` threads the sampling temperature through the Ollama logprobs request (greedy stays 0); tests `tests/test_u35_judgment_mean.py` (fake probability source, offline) | — (doctor's probability-exposure check stays honest: Ollama logprobs is the only verified backend) |
| U36 | Saturation curve reporting | done | Curve rendered (issue #81): `coding/saturation.py:render_saturation_curve` (fixed-width cumulative unique/total table by unit index, ratios at two decimals, middle elided beyond `_RENDER_MAX_POINTS`=32, empty curve -> ""), wired into the release review packet via `research/release/gate_measures.py:gate_saturation` (`value["curve"]` beside the scalar `isr`/`curve_final_isr`), surfacing through `research/release/gates.py:assess` bundles and recorded gate evidence; tests `tests/test_u36_saturation_render.py` (exact render shape + packet wire-in) | — |
| U37 | Active review batching (uncertainty + diversity) | done | `research/review_batch.py:select_review_batch` (active + greedy max-min diversity + always-present random stratum); wired with config-read sizes via `research/review_routing.py:next_review_batch`, re-exported on `research/review_surface.py` | — (P7: CLI/MCP surface rows) |
| U38 | Weak-supervision aggregation (conditional) | absent | no aggregation module (see U33) | P4 (conditional, ≥3 signals) |
| U39 | Label-error triage without Cleanlab | absent | no out-of-sample label queue | P4 |
| U40 | pgvector relaxed-ordering correctness fix | done | U40 pattern (MATERIALIZED candidate CTE, exact outer `ORDER BY score + 0 DESC`) applied to every filtered vector KNN: `handlers/query_handler.py:handle_search` (pgml, ollama-plain, matryoshka arms) + `handle_search_similar`, `research/retrieval.py:_unit_arms` + `_exchange_arms`; regression tests `tests/test_vector_search_ordering.py`; doctor gate `_setup/doctor_research.py:check_pgvector_version`. `research/analytics_sql.py` KNN reads (governance analytics over fixed-shaped candidate sets) intentionally deferred to P3 (different access pattern, no dynamic filters) | P3: analytics_sql sweep + live-PG monotonic-distance test |
| U41 | Risk–coverage thresholding | done | `coding/risk_coverage.py:threshold_for_target_risk` (Clopper–Pearson bound); consumed by `research/review_routing.py:route_for_review` with `routing.*`/`audit.*` from typed config settings — below-threshold routes to review, above auto-accepts with a seeded audit sample, `insufficient_data` routes EVERYTHING to review | — (P7: CLI/MCP surface rows) |
| U42 | Provenance-keyed exact result cache | absent | `handlers/idempotency.py` is command dedup, not LLM result caching | P3 |
| U43 | Structured-output canary + validate/repair | absent | `grep canary` = 0 hits in src (P0 adds the EMBEDDING canary, not the structured-output one) | P1: startup/doctor canary + validate/repair + not_enforced gating |
| U44 | Deterministic clustering inputs | partial | `coding/inductive_cluster.py` accepts seeds (`random_state`/seed plumbing in `coding/_inductive_types.py`); no persisted embedding-matrix hash, no seed sweep in `research/cluster_stability.py` | P1: manifest recording + ≥10-seed sweep |
| U45 | Hybrid lexical+vector retrieval with RRF — evaluate and wire | partial | SPEC HYPOTHESIS ADJUSTED: there is NO `HybridSearcher` class (AGENTS.md is stale on this). Hybrid search is SQL-side: `migrations/006_rrf_fusion.sql:corpus.rrf_fusion` (Σ1/(k+rank), k=60) orchestrated by `handlers/query_handler.py:handle_search` (vector arm + contextual FTS arm + RRF) and `research/retrieval.py:research_search` (two-level RRF, `RRF_K=60`, native FTS `ts_rank`); eval harness absent | P3: `research/retrieval_eval.py` (recall@k/MRR/nDCG@10/p95) on human-reviewed labels before any default flip |
| U46 | halfvec benchmark + optional adoption | partial | halfvec IN the research grain: `migrations/016_research_domain.sql` HNSW on `(embedding::halfvec(1024)) halfvec_cosine_ops` + `research/retrieval.py` halfvec probes + `research/analytics_sql.py`; main-path benchmark absent (`scripts/bench_setup.py` placeholder); main path still full `vector(4096)` | P3: U22 benchmark (recall/size/build/p95) before any main-path adoption |
| U47 | Confidence-signal benchmark | done | signals exist (`coding/uncertainty/` tiers 0–3, `review_priority.py` router) + `coding/confidence_calibration.py` (AUROC/ECE/Brier, Platt/isotonic calibrator, evaluate-once test split); consumed as the routing score by `research/review_routing.py:route_for_review` | — |
| U48 | Evidence-quote verification + multi-run consensus | done | quote verification DONE (`coding/quote_verification.py`); multi-run consensus `research/release/consensus.py:aggregate_runs` wired to the live `research.consensus.runs` via `research/cycle_consensus.py` (cycle inductive stage = N independent passes); reviewer rubric recorded via `research/review_surface.py:execute_rubric_review` + `aggregate_rubric` (ReviewerRubricRecorded events) | — (P7: rubric CLI/HTTP rows) |

## Tallies (final re-tally, 2026-10-12, end-of-program audit)

- **done = 45** — U1–U26, U30–U48 (U23 done as
  benchmark-only; U30 implementation done with adoption data-gated; U45/U46
  done per their evaluate/benchmark-first specs — no retrieval default was
  flipped without evidence; U32/U35/U36 closed by the issue-#81 branch —
  MRL gating, the judgment-mean production call site, review-packet
  saturation-curve rendering; U27/U28/U29 closed by the 2026-10-13 backlog
  wave (#77/#78/#79) — see the end-state table below)
- **partial = 0**
- **deferred = 0** (topic layer/BERTopic remains a separately-tracked idea,
  never a numbered U-row)

48 + 0 + 0 = 48 rows. (This supersedes the earlier 12/15/21 tally, which was
verified against earlier wave branches; the final audit re-verified every
'done' citation by import and sample-checked the partial rows.)

## Addendum (P7 surface-parity wave, 2026-10-12)

0. **Closeout (full-surface-parity wave, 2026-10-13).** The allowlist is
   EMPTY: `research-cycle` and `research-demo` gained their MCP + HTTP
   legs as synchronous long-running calls (`/api/research/cycle`,
   `/api/research/demo`, the `research_cycle`/`research_demo` tools),
   so `assert_full_surface_parity` now holds against the live registry
   with zero exceptions. `TRANSPORT_ALLOWLIST` stays as a mechanism (an
   empty dict, documented in `surface_registry_parity.py`) for a future
   honest gap. The entries below are the historical P7 record.
1. **MECE enforcement is ON.** `surface_registry_parity.assert_parity_outside_allowlist`
   runs against the live registry in `tests/test_surface_registry_consistency.py`
   and `tests/test_surface_parity.py`: every research/release operation must
   be reachable on cli+mcp+http, with ONE documented exception set,
   `surface_registry_parity.TRANSPORT_ALLOWLIST`:
   - `research-cycle` — no HTTP route by design: interactive in/on/out-of-loop
     postures, the blocking approval boundary, and foreground `--watch` are
     not a mechanical request/response.
   - `research-demo` — CLI-only by design (registry rule r13): no MCP tool,
     no HTTP route.
   Both render in the doctor (`corpus-kb doctor` → "surface parity
   (cli+mcp+http)" check, `_setup/doctor_research.py:surface_parity_check`),
   which fails the CHECK (never the doctor exit code) if any other surface
   loses a transport. A stale allowlist entry (its surface gained the
   missing transports) is an error, not dead documentation.
2. **The mechanical sweep.** Eleven pre-existing surfaces got HTTP routes in
   `api/research_http.py` by following the existing adapter pattern (parse
   body → call the shared tool function → serialize): ingest-transcript,
   ingest, coding run, inductive, codebook promote, governance report,
   review execute (accept + override share the route, as they share the MCP
   tool), and the four notebook views. Two were allowlisted instead (above).
3. **`release-request` is a sixth surface, on purpose.** v6 §6 names five
   commands, but nothing in the repo ever opened a `draft_candidate` release
   (the service existed; no surface called `request_release`), so four of
   the five commands had nothing to operate on and `status` could not name
   a runnable next action. `release-request` is registered with full
   parity like the others and documented in the harness pack.
4. **`release-execute` records evaluated gate results.** Gates are pure
   functions over evidence; executing a release composes the SAME bundle
   `assess` reports, records each result on the DRAFT
   (`ReleaseService.record_gate_result`), applies explicit `--waive
   GATE=justification` entries, then transitions under the profile's
   enforcement. Recording is bookkeeping of deterministic evaluations — no
   new evidence is invented, and a refusal is structured data (CLI exit 1,
   HTTP 409), never a crash.


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

## Known line-limit deviations (250-line soft limit)

| File | Lines | Why it stays |
|---|---|---|
| `research/retrieval.py` | ~719 | Pre-existing; the U21 arm logic is inseparable from the existing arm SQL (spec v8 acknowledges). |
| `domain/codebook.py` | ~376 | Eventsourcing 9.5 rejects `@event` methods on mixins, and the binding spec requires extending this exact aggregate. |
| `coding/confidence_calibration.py` | ~308 | AUROC/ECE/Brier + calibrator fit + evaluation are one numerical contract; splitting would scatter the test-eval-once rule. |
| `coding/alt_test.py` | ~288 | Paper-exact implementation (indicators, FDR, AP measure) kept inspectable in one module per the U13 isolation requirement. |
| `coding/aggregation.py` | ~277 | Dawid-Skene EM and weak-supervision aggregation share the posterior machinery (U33/U38). |
| `research/release/service.py` | ~253 | Release lifecycle transitions + compare; projector interaction tested as a unit. |
| `handlers/release_handlers.py` | ~720 (P7) | ONE shared handler per v6 §6 operation plus the typed payload dataclasses that keep the three transports byte-identical; splitting payloads from handlers would put the D4 schema pin in two files. |
| `cli_release.py` | ~470 (P7) | The six-command release group: flag parsing, packet/status renderers (guide_copy-driven), and exit-code mapping in one surface file; `cli.py` stays the shared `_run_async` owner. |
| `api/research_http.py` | ~290 (P7) | The declarative route table for 17 research/release routes; the table IS the parity surface (`tests/test_surface_parity.py` pins it against the registry). |

## End-of-program audit (2026-10-12, final per-U verdicts)

The Step-0 table above is the historical snapshot this program started from (kept for the audit trail). The table below is the verified END state: every 'done' citation was import-checked against the merged tree (`feature/kb-u48-wave2-integration` @ 9ec1d2a, now master b0997bd) by the final adversarial audit. Tallies as of the 2026-10-13 backlog wave: **done 48 / partial 0 / deferred 0** (the 2026-10-12 audit closed at 42/3/3; the #81 branch flipped U32/U35/U36 and the #77/#78/#79 wave flipped U27/U28/U29).

| U# | Final | Evidence |
|---|---|---|
| U1 | done | `migrations/019_codebook_releases.sql` (4 tables, RLS FORCE, immutability trigger) + `research/release/events.py` + `projections/research/release_projector.py`; replay-determinism test |
| U2 | done | `research/release/manifest.py` (`canonical_json_bytes`/`canonical_sha256`, byte-identical determinism test) |
| U3 | done | `research/release/profiles.py` (3 profiles, `enforcement=warn` default); 3-way config lockstep test |
| U4 | done | `research/release/gates.py:assess` composes the 19-gate registry; writes nothing; honest blocking counters |
| U5 | done | `handlers/release_handlers.py` (6 ops) on cli+mcp+http via `surface_registry.py`; `corpus-kb release ...` / `release_*` tools / `/api/release/*` |
| U6 | done | `ReviewPacket` + `_print_review_packet`; generated `corpus-release.md` harness card (drift-gated) |
| U7 | done | `research/release/gate_codebook.py:gate_definition_completeness` |
| U8 | done | `research/release/gate_partitions.py:gate_boundary_agreement` (segment F1, label agreement excluded) |
| U9 | done | `research/release/gate_partitions.py:gate_partition_independence` (unit/doc/participant disjointness) |
| U10 | done | `research/release/gate_machine_profile.py:gate_machine_profile` (`REQUIRED_MACHINE_PROFILE_FIELDS`) |
| U11 | done | `research/release/gate_performance.py:gate_heldout_per_code_f1` (per code and per stratum) |
| U12 | done | `coding/uncertainty/prompt_stability.py` + `gate_annotators.gate_prompt_stability` |
| U13 | done | `coding/alt_test.py` (exact binomial + Benjamini-Yekutieli FDR; arXiv:2501.10970) + gate |
| U14 | done | `coding/ppi.py:ppi_estimate` (raw labeled `uncorrected`) + `gate_prediction_powered` |
| U15 | done | `research/release/gate_performance.py:gate_stratum_disparity` (min-stratum-size not_evaluable) |
| U16 | done | `research/cluster_stability.py:top_term_jaccard` + `gate_stability.gate_content_stability` (ARI reported separately) |
| U17 | done | `gate_stability.py:gate_audit_sample_design` + `audit_partitions` (stratum/inclusion-probability/seed) |
| U18 | done | `coding/uncertainty/cross_run_agreement.py` + `gate_routing.gate_agreement_calibration` (AUROC/ECE before gating) |
| U19 | done | `gate_routing.gate_agreement_routing` + `cycle_gates.gate_gray_zone_from_agreement` + `review_routing.route_for_review` |
| U20 | done | `research/search_settings.py` (SET LOCAL, `supports_iterative_scan` fails closed) + `search.hnsw.*` |
| U21 | done | `research/retrieval_settings.py:selectivity_ratio`; exact arms in `query_handler` + `research/retrieval.py`, fail-open |
| U22 | done | `scripts/bench_vector_types.py` (full/halfvec/256-d probe/scan modes; binary quantization documented-rejected) |
| U23 | done (benchmark-only) | `scripts/bench_pg_textsearch.py` (gated, honest `not_enforced`); native FTS unchanged; pg_search AGPL never touched |
| U24 | done | `research/deduction_guard.py:decide_guard` (off/warn/enforce + sandbox labeling) + `unreleased_draft` halt gate |
| U25 | done | `research/monitoring.py:propose_from_alarms` -> `ReleaseService.propose_change`; released codebooks never mutated |
| U26 | done | `licenses.lock.json` + `scripts/generate_license_lock.py` + CI License baseline check |
| U27 | done | `coding/refi_qda/` (stdlib-XML export/import, canonical-hash manifest sidecar surviving round-trip); XSD validation against the pinned `Project-mrt2019.xsd` fixture (3-mirror SHA-256) behind `pytest.importorskip("lxml")`; `tests/test_refi_qda.py` (#77, 2026-10-13 wave) |
| U28 | done | `tests/test_mapie_crosscheck.py` — MAPIE (dev-extras only, `importorskip`, never runtime) cross-checks `research/calibration.py:conformal_sets` on shared seeded splits; per-seed coverage agreement within documented tolerance + divergence notes (#78, 2026-10-13 wave) |
| U29 | done | `scripts/bench_gatos_baseline.py` / `bench_tntllm_baseline.py` / `bench_lloom_baseline.py` (deterministic, offline, honest `insufficient_data`) + `docs/research/benchmarks/inductive-baselines.md` with observed recovery-baseline results; recovery-baseline caveat pinned in `--help` (#79, 2026-10-13 wave) |
| U30 | done (adoption data-gated) | `rag/reranker.py` qwen3 yes/no logprob scoring; `create_reranker` wired in `query_handler`; Ollama logprobs verified live |
| U31 | done | `scripts/bench_embedders.py` + versioned `EmbeddingProfile` contract (never in-place embedder changes) |
| U32 | done | two-stage matryoshka config-off + MRL gating enforced: `config.py:matryoshka_mrl_error` (load_config raises), `embedding.mrl_trained` default false in all three config locations, `validate_configs.validate_runtime_config` cross-field rule; `tests/test_u32_mrl_gating.py` (#81) |
| U33 | done | `coding/aggregation.py:dawid_skene` (numpy EM, posterior uncertainty) |
| U34 | done | `gate_machine_profile` coder-panel family diversity (fail in high-assurance, warn elsewhere) |
| U35 | done | `coding/judgment_sampling.py:sample_judgments` (4 extra 0.7-temperature draws via the Ollama logprobs backend) called from `coding/coder_dispatch.py:dispatch_chunk` behind `research.judgment_distribution.use_mean`; off = byte-identical single greedy call; `tests/test_u35_judgment_mean.py` (#81) |
| U36 | done | `coding/saturation.py:render_saturation_curve` wired into the review packet via `research/release/gate_measures.py:gate_saturation` (`value["curve"]`); `tests/test_u36_saturation_render.py` (#81) |
| U37 | done | `research/review_batch.py:select_review_batch` wired via `review_routing.next_review_batch` |
| U38 | done (conditional) | `coding/aggregation.py:aggregate_signals` (DS-EM at >=3 signals; `insufficient_signals` -> vote, tie->review) |
| U39 | done | `coding/label_triage.py:label_triage` (stratified k-fold OOF, ranked queue, `precision_of_flags`) |
| U40 | done | MATERIALIZED CTE + outer `ORDER BY score + 0` in `research/retrieval.py` + `handlers/query_handler.py` + `research/analytics_sql.py`; `tests/test_vector_search_ordering.py` |
| U41 | done | `coding/risk_coverage.py:threshold_for_target_risk` (exact Clopper-Pearson bound) consumed by `review_routing` |
| U42 | done | `research/exact_cache.py:compute_cache_key` (6-component SHA-256) + migration 020 + rollback; live-PG test |
| U43 | done | `coding/structured_output.py` (adversarial `run_canary`, validate/repair, `enforcement_blocks`) + gate + doctor |
| U44 | done | `research/release/seed_sweep.py` (>=10-seed sweep, `content_hash_matrix`, `clustering_provenance`) + `GATE_CLUSTER_DETERMINISM` |
| U45 | done (as specified) | `research/retrieval_eval.py` (recall@k/MRR/nDCG@10/p95; honest `insufficient_data`; no default flipped) |
| U46 | done (benchmark) | halfvec benchmark + tested `adoption_decision` gate; cutover deferred -> issue #76 |
| U47 | done | `coding/confidence_calibration.py` (AUROC/ECE/Brier, Platt/isotonic fallback, evaluate-once-on-test) |
| U48 | done | `coding/quote_verification.py:classify_quote` + `research/release/consensus.py` + rubric via `review_surface.execute_rubric_review` |

---

## Ingestion rigor & strategy registry program (2026-10-12, feature/ingestion-rigor)

A six-stage parallel program on top of the U1–U48 close-out, executed in
isolated worktrees and integrated S2 → S1 → S3 → S4 → S5 → S6.

| Stage | Outcome | Contract evidence (plan-pinned) |
|---|---|---|
| S1 Extraction truth | Honest extraction accounting (requested/kept/dropped-by-cap/dropped-by-confidence), entity provenance `{extractor, model, revision, confidence}`, opt-in GLiNER2 backend (`fastino/gliner2-large-v1`, six mention types, no relations), `extraction/eval.py` span harness with promotion gate (micro F1 >= 0.80 AND >= baseline + 0.02), dead `bert_backend.py` removed (factory never routed to it), ontology optional `version` + per-type `description` | `extraction/gliner_backend.py`, `extraction/eval.py`, `extraction/protocol.py:ExtractStats`, `extraction.gliner.*` config, `pyproject.toml` `ner` extra |
| S2 Ingest integrity | `ingest_runs` ledger (running → completed/failed with start/finish hashes + per-stage jsonb), `chunks.degraded` + `chunks_vectors.degraded` flags, ledger-aware short-circuit (only clean completed runs skip), advisory-lock concurrency with honest `skipped: concurrent-run`, vector COALESCE overwrite fix, projection file_hash clobber fixes, store split into `tools/ingest_store.py` | `migrations/022_ingest_integrity.sql`, `tools/ingest_store.py:PostgresIngestStore` |
| S3 Search honesty | `search.exclude_degraded` (default true) filters degraded rows in candidate CTEs only (seeds never filtered), all-zero query guard abstains the vector arm, `degraded_excluded {count, reasons}` envelope + `degraded_empty` status with the pinned remedy, `build_embedding_input` single-sourcing + reembed helper (repaired rows flip degraded=false; honest scanned/fixed/failed), doctor gains degraded/ledger/gliner checks | `handlers/query_handler.py` (envelope + candidate filters), `search.exclude_degraded`, reembed handler |
| S4 Strategy registry | `IngestionStrategy` protocol (`detect` → `DetectResult(confidence, reason)`, `build_chunks` → `ChunkPlan`), `register_strategy()`/`resolve_strategy()` with precedence explicit `--source-type` > `ingestion.strategies[source_type]` > content detection > `ingestion.default`, entry-point group `corpus_kb.ingestion_strategies`, built-ins `default` (verbatim behavior) + `transcript` (turn-aligned Q+A, `qa_context {question, participants, exchange_id}`), migration `023_qa_context.sql` (+GIN; FTS inclusion gated by `search.qa_context_in_fts`, default FALSE — `ts_rank` has no IDF), conformance suite parametrized over the registry, result dict gains `strategy` + detection provenance | `ingestion/base.py`, `ingestion/registry.py`, `ingestion/strategies/{default,transcript}.py`, `ingestion.strategies`/`ingestion.default`/`search.qa_context_in_fts` config |
| S5 Front door + surfaces | Top-level `corpus-kb ingest` (file/dir, `--source-type`, `--watch` with embedding-unavailable-only auto-heal + backoff + `--no-heal`, `--reembed`, `--limit`, `--doc-id`, `--json`), exit codes 0 ok / 1 partial / 2 failed / 3 config, MECE surface registration for `ingest_status` (CLI+MCP+HTTP GET `/api/ingest/status`) and reembed (CLI+MCP+HTTP POST `/api/ingest/reembed`) across registry, parity checks, editor configs, harness pack, snapshots | `cli_ingest.py`, `surface_registry.py` + `surface_registry_parity.py`, `/api/ingest/status` + `/api/ingest/reembed` routes |
| S6 Docs sweep | MCP-ingestion claims corrected across FAQ/getting-started/INGESTION/FEATURES/API/README (MCP-over-stdio honestly still not implemented; ingest/status/reembed now real CLI+HTTP surfaces with registered MCP names), new `docs/INGESTION-STRATEGIES.md` authoring guide, config keys documented in INGESTION.md, AGENTS.md env-table note (no new env vars) | `docs/INGESTION-STRATEGIES.md`, the six doc files, `AGENTS.md` |

Known lies corrected by S6 (pre-existing claims that contradicted reality):
INGESTION.md quick start told users to "start the MCP server" with bare
`corpus-kb` (stdio exits with a message); FAQ claimed search "falls back to
full-text only" on Ollama outage (degraded chunks are now flagged and
excluded, not silently searched); README claimed BERT as a live extraction
backend (dead code, factory never routed to it) and "MCP tools" as a live
surface; API.md listed `corpus-kb --transport stdio` as a working protocol.

Follow-ups filed at close: ASR / web-crawl / slides / PII / Splink as future
strategy modules; `qa_context_in_fts` retrieval eval; GLiNER2 promotion
decision (needs eval numbers on the real corpus); qwen3-embedding switch;
#17 full transactional store.
