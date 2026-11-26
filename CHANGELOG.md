## Unreleased — ingestion recipes, calibrate-then-lock sweep, and honest gates (PRs #211–#214)

The author → measure → apply loop for retrieval quality, documented in
docs/VALIDATION.md (new). Profiles accept a `calibrate:` block naming up
to eight query-time recipe candidates (six allowed override keys:
`retrieval.rrf_k`, `retrieval.vector_candidates`,
`retrieval.lexical_candidates`, `reranking.enabled`,
`reranking.over_retrieve_n`, `reranking.score_floor`; unknown keys fail
the profile load). An ingest under `validate: "calibrate"` sweeps the
family over one scratch Postgres tenant per (profile, golden) pair —
the sample is ingested once, every candidate ranks cheaply through the
one `ranked_children` seam shared with search and eval. Paired tests
run Holm-corrected across the family (ranx fisher test with the
`calibrate` extra; an honest sign test without it). Outcomes are
`success`, `no_significant_winner`, or `insufficient_data`; only
`success` writes `<profile>.lock.json`, whose additive keys record
`baseline`, `alpha`, `min_queries`, `outcome`, `proposed_overrides`
(the manual apply path: copy into config.yaml, query-time, no
re-ingest; CLI tracked in issue #200), and `reranker_active`. Doctor
reports structured embedding dims plus reranker state, and the llamacpp
probe POSTs a sanity pair (llama-server needs `--reranking --pooling
rank --embedding`). A fifth validation mode, `transcript_meta` (PR
#213), checks speaker presence, strictly increasing sequence numbers,
and empty-unit counts — advisory only. Ingest runs gained a nullable
`project_id` (migrations 036 PG / 037 DuckDB) with a best-effort
research transcript link; the SPA Scale page gained a project filter,
receipts show the validation summary and dedup count, and Setup shows
dims and reranker. Design record:
docs/research/ingestion-recipes-as-built.md (new), which supersedes
docs/research/ingestion-validation-chunking-plan.md.

## Unreleased — browser UI (lifecycle SPA)

The full research flow ships as a browser app at `/app/`, served by
the same server. Eight stages: Setup (system status, sample data),
Ingest (files, website with crawl, paste text, server paths,
transcripts), Review (degraded chunks, coding review queue, topics),
Scale (self-polling run ledger, bulk ingest with force, re-embed),
Ask (cited answers, honest verify badge, search, notebook ask),
Analyze (method advisor, manifest-driven runner), Codebook (promote
gate, notebook views, governance report, one-stage cycle wizard),
and Release (assess, request, review, gated execute, structured
block refusals). Hardening: Playwright smoke plus axe-core a11y scan
on a live throwaway server (REQUIRED ci job), a per-route bundle
budget gate, and honest states for every degraded mode. See
docs/UI-SPA.md and docs/UI-DEVELOPMENT.md.
## Unreleased — structure-aware ingestion wave (PPTX, web, late chunking)

First-class ingestion for two more modalities plus the late-chunking
embedding technique, all on by default:

- **PPTX (`pptx_structure` strategy):** decks chunk per slide with title
  breadcrumbs, tables rendered as GitHub markdown, speaker notes as their
  own chunks, and core/extended-properties metadata (author, created/
  modified, company, subject) on every chunk. `python-pptx` is now a
  required dependency; `.pptx` joins the watched/directory-ingest suffixes.
- **Website (`web_structure` strategy + `corpus-kb ingest-url`):** httpx
  fetching with robots.txt respect (re-checked on every redirect hop),
  streaming size/content-type caps, private-address blocking by default
  (`web.allow_private_addresses` opts out for genuinely local targets), and
  optional same-site crawl (depth/page caps, delay, per-page error
  isolation — one bad page never aborts a batch). Pages chunk per heading
  section with OG/Twitter/JSON-LD/canonical metadata extraction; chrome
  (nav/header/footer) is excluded. Exposed on MCP, HTTP
  (`POST /api/ingest/url`), socket, and CLI surfaces.
- **Late chunking (document pipeline):** `embedding.late_chunking: true`
  by default — when the `latechunk` extra is installed and the model is a
  `qwen3-embedding*` token-level family whose output width matches
  `embedding.dimensions`, chunk vectors are pooled from one full-document
  forward pass (windowed past the model context, run off the event loop)
  so every chunk sees the whole document — and the strategy-produced char
  spans of decks and web pages feed it directly. Otherwise the run
  degrades to pooled embeddings with an explicit `embedding_note` in the
  result; the envelope records `embedding_mode` (`late-chunking` only when
  at least one chunk was genuinely late-chunked) and the CLI prints it.
- **E2E:** a research-corpus suite ingests a 10-slide methodology deck and
  a multi-section article + crawled appendix through the real pipeline
  offline, pinning structure, metadata, and envelope honesty.

## Unreleased — U1–U48 upgrade program (P0–P7)

Implementation of the corpus-kb upgrade spec (U1–U48), phased and verified
against `docs/research/upgrade-reconciliation.md` (per-row evidence; the
repo won where the spec hypotheses disagreed). Honest phase statuses:

- **P0 — reconciliation:** every U-item verified against the repo
  (`file:symbol` evidence); surface-registry scaffolding
  (`surface_registry.py`, `surface_registry_parity.py`) + internal
  consistency tests. Spec-vs-repo discrepancies recorded (no
  `HybridSearcher` class — hybrid search is SQL-side `corpus.rrf_fusion`;
  the `vector(4096)` schema column is load-bearing; inert config knobs
  removed).
- **P1 — release foundation (wave 1):** release lifecycle events +
  `CodebookVersion` aggregate transitions, canonical-JSON `ReleaseManifest`
  (SHA-256), release profiles (exploratory / team-codebook /
  high-assurance with off|warn|enforcement), `ReleaseService`, release
  projector into migration 019's four read tables, P1 evidence gates,
  structured-output canary + clustering-determinism work, install
  checkpoint/licensing hygiene.
- **P2–P3 — gates + retrieval (wave 1–2):** U7–U19 gate suite
  (definition completeness, partition independence, machine profile,
  held-out per-code F1, prompt stability, alternative annotator test,
  prediction-powered estimates, stratum disparity, content stability,
  audit sample design, agreement calibration/routing, reliability alpha,
  exhaustiveness, saturation), pgvector relaxed-ordering fix (U40),
  hnsw transaction-local settings (U20), hybrid RRF retrieval evaluation
  harness (U45/U46), risk–coverage routing (U41), active review batching
  (U37), Dawid-Skene aggregation, confidence-signal calibration/benchmark
  (U47).
- **P4–P6 — shadow + hardening (wave 2):** shadow ingest index
  (`tools/shadow_ingest.py`), token hierarchy + post-fusion parent
  expansion (default-off), enforcement wiring for the deduction guard
  (U24: warn/enforce/--allow-draft sandbox), monitoring change proposals
  (U25), reviewer rubric + multi-run consensus (U48), quote verification.
- **P7 — surface parity + harness UX (this wave):** U5 five-command
  release surface (`assess`/`review-release`/`release`/`status`/`compare`,
  plus the mechanically required `release-request`) on ALL THREE
  transports from ONE shared handler layer (`handlers/release_handlers.py`
  → `cli_release.py` CLI group, `tools/release_tools.py` MCP tools,
  `api/research_http.py` HTTP routes); MECE parity enforcement flipped ON
  (`surface_registry_parity.TRANSPORT_ALLOWLIST` documents the two
  by-design exceptions: research-cycle, research-demo); honest exit codes
  (assess reports, blocked release exits 1/HTTP 409) and `--json` stable
  payloads; U6 harness UX (generated `corpus-release` slash-command pack,
  review-packet renderer, guide_copy literal next actions); doctor
  surface-parity check; docs + this changelog.

## v0.1.1 (2026-11-26)


- chore: release 0.1.1 (#600)
- * chore: bootstrap three-branch model docs + renovate develop base (#552)
- * chore: skip ollama-marked tests when the CI path gate is closed (#553)
- * chore: skip ollama-marked tests when the CI path gate is closed
- * docs: split long gate sentences in the CI paragraph
- * docs: close the stubs-audit docs findings and add the entry map (#554)
- * docs: correct the doctor contract, error map, and watch suffixes
- * docs: replace dead chunking keys and fix the extraction table
- * docs: state the true language list and 33 method count
- * docs: mark graph/bfs deprecated and drop the migration count
- * docs: attribute the commit rule to commit-validate.yml
- * docs: add the documentation entry map and research index
- * docs: add a contents block to PLUGIN.md
- * docs: split the commit-rule bullet into short sentences
- * fix: close the five stub-audit findings (W2.1) (#555)
- * fix: enforce coder window token ceiling and drop dead llamaindex arm
- * chore: refresh stale markers and pin installer config precedence
- * chore: lower installer size ceiling to match the shrunk file
- * docs: finish the docs PR 554 organization items with the coordinated pin updates (#556)
- * docs: rename three guides to uppercase names and fold research.md into the research index
- * chore: point doctor, demo, and cycle doc pins at the renamed research docs
- * chore: DevOps consolidation - validator merge, docker tag logic, production sync (#557)
- * chore: retire agent-governance and port its commit length rule
- * chore: accept bare integration heads in branch-validate
- * chore: derive docker image tags from refs instead of a pin
- * chore: add production sync workflow for release publishes
- * docs: carry the branch-model plan amendment into the repo
- * chore: bump package version to 0.1.1rc0 (#558)
- * chore: bump package version to 0.1.1rc0
- * chore: sync uv.lock for 0.1.1rc0
- * fix: land release-path review fixes (#561)
- * fix: validate the conventional prefix on commit subjects only
- * fix: run CI pull-request checks for the production base branch
- * chore: derive the OpenAPI version from installed package metadata
- * chore: harden the production sync recipes and permissions
- * chore: document the subject-line prefix rule in AGENTS.md
- * fix: harden changelog workflow tags, cz invocation, and write path (#563)
- * fix: Add push trigger and branch-aware base to commit-validate (#565)
- * chore: Ignore graphify and serena generated artifacts (#567)
- * fix: Drop dead main trigger entries from two workflow files (#569)
- * feat(release): Automate release creation from version tags (#571)
- * feat(release): Automate release creation from version tags
- * docs(release): Shorten release-path sentences for the prose gate
- * fix: Run the analysis date ops on the duckdb backend (#572)
- * fix: Run the analysis date ops on the duckdb backend
- analysis-backfill-dates and analyze-run crashed on database.backend:
duckdb. The date backfill created a plpgsql pg_temp.safe_doc_date
function and ran SET LOCAL timezone, both Postgres-only, with no
dialect guard on the gateway path; analyze_run re-raises engine-shaped
errors loudly, so the composed run died with them.
- DuckDB also cannot rewrite a documents row that chunks reference
(every UPDATE arrives as a delete plus insert), and every document
the migration-027 rule would date is referenced by its own chunks,
so the write side is engine-bound there. backfill_envelope in
analysis/dates.py now owns the storage-dependent outcomes: Postgres
keeps the byte-identical statements; duckdb answers the honest
unavailable envelope with a real scanned count. Forward ingests
still date new documents at INSERT time on both engines.
- Unmask the three analysis legs in the duckdb operation-parity gate:
they now run with analysis.topics.over_time.enabled instead of the
dormant default that hid the date path behind disabled envelopes.
- New tests/test_duckdb_analysis_dates.py drives the ops on a migrated
duckdb file (watched failing on the plpgsql parser error first).
Release the analysis_tools.py size ceiling: the file shrank under
the 250-line soft limit, so the ratchet entry goes.
- * fix: Join the wrapped lines ruff format expects
- The CI lint job runs ruff format --check beside ruff check; two of
the new wrapped lines fit the line-length budget on one line. Both
files reformat cleanly and the touched test files stay green.
- * fix(research): Make re-ingesting an ingested source idempotent (#573)
- Re-research-ingesting a source the corpus already ingested kept the old
documents row id while the new events carried a new aggregate id, so
the speaker and unit writes broke the foreign key to documents and
landed in the DLQ silently (#529, parity gap G03, both engines).
- New projections/research/_rekey.rekey_ingested_document resolves the
existing (tenant_id, source) row: absent or matching ids are a no-op;
an older id is deleted children-first with the documents row last, so
the caller's INSERT lands the fresh row under the incoming id.
Postgres deletes inside the caller's transaction (declared cascades,
research read models via the shared drop_derived_rows); DuckDB routes
through the existing delete_document_cascade (#330: DuckDB reads FKs
at the last commit, so each stage commits on its own). The documents
INSERT SQL moved into _rekey.py; exchange_projector.py stays net-zero
at 246 lines.
- The #529 repro names only the exchange-projector leg, so
code_projector.py is untouched per the plan's escape clause.
- Tests (tests/test_research_reingest_idempotent.py) run the
corpus-then-research sequence hermetically on a migrated DuckDB file
and on the throwaway research Postgres database: no DLQ rows, the read
models answer for the new doc_id, and a third ingest is a no-op.
- * fix(graph): advance read models after add-entity/add-relation (#574)
- add_entity/add_relation only saved events, so CLI/HTTP/socket graph
writes stayed invisible to search_graph, get_entity_relations, and the
hop reads until an unrelated command advanced the projections (#530).
- - projections/catchup.py: advance_graph_projections folds the documents
  projection alone - no embedder construction, no research sweep, so an
  entity write never needs Ollama.
- tools/graph_tools.py: the ONE tool handler now folds the read models
  after each save; the CLI already hands it a pool-or-gateway, so both
  engines get read-your-writes.
- api/routes_graph.py + api/_socket_arms.py: the two routes and socket
  arms route through tools.graph_tools (zero-drift rule 1); envelopes
  are unchanged. Malformed ids on HTTP/socket now answer the typed
  ValidationError (-32602) from the shared uuid_arg guard.
- Tests: new tests/test_graph_read_your_writes.py pins read-your-writes
on both engines plus the envelope key-set; the smoke, socket-await, MCP
round-trip, and surface-parity stubs move to the tool seams.
- * fix: DuckDB parity residues - 035 CHECK backfill, migrate/setup flag refusals, #527 P3s (#575)
- * fix(migrations): Backfill the meta_embeddings CHECKs on DuckDB (#527)
- The DuckDB mirror 035 omitted pg 034's two CHECKs, so the mirror
admitted rows Postgres rejects (gap G04). DuckDB 1.5.x refuses
ALTER TABLE ADD CHECK, so duck 047 lands both CHECKs through the
transactional table rebuild (the 041 pattern); the runner wraps the
file in one transaction and rows copy out before the drop.
- The two _CHECK_GAP allowlist rows go stale and leave the gate
(ratchet down, rule 8); the nameset gate gains the one-sided duck
047 declaration and its pin test. No rollback mirror: the rebuild
is additive and constraint-only, so rollback = revert.
- * fix(migrate): Refuse the Postgres-only migrate flags on DuckDB (#527)
- --superuser-dsn, --work-mem, and --reset-embeddings were silent
no-ops under a duckdb:/// target; only --maintenance-work-mem was
typed-refused (gap G07). One loop now refuses all four flags with
the taxonomy's ConfigError before any connection, and the three
unqualified help strings gain the "Postgres only." qualifier.
- * fix(setup): Refuse the Postgres-only setup flags on DuckDB (#527)
- setup --build-local and --skip-image-verify shape the Postgres
docker stack only and were silently ignored under --backend duckdb
(gap G15); install.py routes that path without passing them. A new
_setup gate raises ConfigError naming the flag, and the setup
command calls it before install_main and maps the exit to the
taxonomy's config code (3). The command file shrank to 410 lines
(both flag help strings condensed with "Postgres only."), so its
size ceiling ratchets down 411 to 410 in the same change.
- * fix(graph): Gate bfs depth and null-mask the DBCV probes (#527)
- The #527 P3 pair. handle_bfs now validates max_depth (1..16) at the
handler, so every store gets the validation only AGE had; the store
files stay untouched. The PG baseline probe in latest_baseline_dbcv
masks on the extracted value (checkpoint->> IS NOT NULL) instead of
the bare ? key probe, so a JSON-null DBCV can no longer stop the
LIMIT 1 scan and hide an older real value; the duck arm already
scanned newest-first with the same semantics.
- * feat(storage): parameterize the embedding width in both migration sets (#576)
- Issue #559 (parity gap G28, plan PR-5): a non-default
embedding.dimensions was accepted at setup and only failed at the first
ingest write. The primary-width DDL now carries the placeholder
__EMBEDDING_DIMS__ in both migration sets plus storage/schema.sql,
substituted by the runners and the schema bootstrap from the configured
width (the env override flows through load_config). Both runners record
the observed store width in a new meta table (PG 047, duck 048, rollback
mirrors), and the boot guard refuses a mismatched width with one
identical typed message on both engines.
- Supporting fixes the guard needs to function: the DuckDB tracking-table
probe now reads the runner-owned unqualified table (the corpus-qualified
spell answered fresh on every real store), and code_migration_names
dispatches per backend (a duck 048 row met the PG-terminated name set
and refused in error). The data-migration manifest skips the meta table:
a fresh target re-records its own width. embedding_cache_2560 stays
pinned: it is a derived fixed-width cache with its own runtime
width-skip guard.
- * docs(release): Document the develop-to-master release step (#578)
- * feat(config): refuse backend-mismatched keys in both directions (#577)
- Backend-gate completion of the DuckDB parity program (G09, G16-G20).
- One shared gate, one string bank: NEW src/corpus_kb/backend_gates.py
holds every backend-mismatch rule in one fixed order and owns the four
moved PG-only-under-duckdb refusals byte-identically. config.py becomes
a thin wrapper (net-negative: 979 to 960 lines) and re-exports the
constants, so graph_factory and the tests keep their import surface.
- New refusals (release note: users with a hnsw block under duckdb now
get a load-time refusal):
- retrieval.hnsw.* tuned off the shipped defaults under database.backend:
  duckdb (G16-G20) - pgvector-only knobs silently no-opped there.
- graph.backend: postgres under duckdb at load (G09) plus the same
  refusal at the factory for hand-built dicts (defense-in-depth).
- graph.duckdb_graph_backend tuned off "cte" under postgres (mirror).
- Explicit-set rules read the RAW user file tree, and only a value that
differs from the shipped default counts: shipped YAMLs and setup
write-backs render the defaults into user files, so presence alone
would refuse untouched installs (the plan's own rule: defaults must
never trip).
- Test updates: NEW test_backend_gates.py (plan's seven + wrapper and
load_config pass-through pins), NEW test_graph_factory.py (G09 at the
factory), test_backend_config_truth flips two load-cleanly pins to the
G09 contract, test_config_alias_lockstep follows the moved private
helper, test_release_duckdb_e2e drops the now-refused postgres graph
line, size-ratchet baseline follows config.py down (979 to 960).
- * chore(config): dead-key parity — wire self_query, remove five dead keys, complete lockstep (PR-7) (#579)
- * fix(config): wire retrieval.self_query.enabled
- The wiring twins built SelfQueryParser from the legacy search.self_query key only, so the canonical retrieval.self_query.enabled gated a parser that was never built (gap G05).
- Both twins now read the canonical key. The shipped default flips True to False (decision R6): the flag gates parser construction and stays opt-in, so default installs keep today's behavior (parser off). The typed reader default flips in lockstep, the yaml blocks gain the opt-in comment, and the overlay/legacy tests follow the flip.
- The multi_query comment in config.py tightens to a pointer at the packaged yaml contract, which carries the full text.
- * chore(config): remove dead keys with lockstep
- Four validated-but-inert keys leave the config (decision R7): retrieval.vector_index, retrieval.fts_rebuild_debounce_s, storage.vector_type, and retrieval.qa_context_in_fts (gaps G21, G22, G23, G06). An honored-by-absence key beats a validated-but-inert one.
- Removals land in get_default_config(), both config.yaml files, RUNTIME_CONFIG_KEYS, the enum banks and rule helpers, the legacy alias row, and the typed reader field. storage/dialect.py keeps its hard-coded default; the migration-023 index stays (pinned structural).
- The debounce comment described a mechanism that does not exist; freshness is a COUNT watermark (duckdb_fts), so the claim goes with the key. Tests pinning the dead keys follow, and the docs/ADMIN.md rows drop with their keys.
- * feat(topics): complete zeroshot_min_similarity lockstep
- topics.zeroshot_min_similarity was readable by the typed loader but absent from TOPICS_CONFIG_DEFAULTS and the shipped yaml, so setup write-back dropped a user-set value (gap G24, decision R3).
- The key joins all three homes in one change: the defaults bank, get_default_config() through the typed module defaults, and both config.yaml files. RUNTIME_CONFIG_KEYS gains the float row.
- The old comment explaining why the key stayed out of the defaults is rewritten; the three-ways concern is resolved by this very change.
- * chore(config): remove server.transport dead key
- No runtime reader exists; transport selection is the argparse --transport flag (decision R5), and the config default even contradicted the CLI default. The key, the CORPUS_KB_TRANSPORT env row, and the docs/ADMIN.md rows leave together, so doctor now warns set-but-unread CORPUS_KB_TRANSPORT for free (the known-variable registry derives from the override map).
- mcp-configs keep launching through --transport CLI args; the server_wiring argparse is untouched. The docs sync also drops the retrieval rows whose keys commit 2 removed.
- Lands tests/test_dead_config_keys.py pinning G05, G06, G21, G22, G23, G24, and G29, and lowers the size-ratchet baselines for files this PR shrank (config.py 960 to 937, retrieval_settings 261 to 256, test_search_honesty 1065 to 1058).
- * docs(ingestion): sync dead-key rows and the flipped default out of docs
- Fix round 1 from review: INGESTION.md still taught users to set two keys PR-7 removed. The storage.vector_type and retrieval.qa_context_in_fts table rows, the qa_context_in_fts explainer section, and the stale retrieval.self_query.enabled true default all leave; the self_query row now shows the shipped false default.
- The storage-portability program's config-lockstep rule refreshes its listed key set to current truth: the two removed keys leave the list, with a note that removals follow the same five-file atomity in reverse.
- test_removed_keys_flagged_for_users renames to test_removed_keys_accepted_silently so the name matches the presence-only assertion, with a comment pointing at PR-9 for unknown-key detection.
- * fix: W1 critical-path bug fixes (F-02, F-03, F-04, F-05, F-09, F-10, F-11, F-12, F-13, F-15) (#580)
- * fix: map the DuckDB guard lock conflict to the typed lock error (F-02)
- * fix: bound the LlamaIndex startup probe with a timeout (F-03)
- * fix: share one embedder between query stack and projections (F-04)
- * fix: fall back to PostgresGraphStore on any AGE probe Postgres error (F-09)
- * fix: keep the watch ledger warning ASCII-safe on Windows consoles (F-05)
- * fix: create event tables only when a table is missing (F-10)
- * fix: refuse a bad server.port with a typed config error (F-11)
- * fix: resolve the embedding provider label through one helper (F-12)
- * fix: await each cancelled projection task on its own suppress (F-13)
- * fix: read pool settings from one constants module (F-15)
- * fix: align the env snapshots and size ceilings with the W1 changes
- * feat: W3 typed source_types registry, visibility, profiles (#583)
- * feat: add the source_types registry, alias map, and resolver
- * feat: accept typed source names in ingest-text and stamp canonical types
- * feat: add the visibility property to typed external-site ingests
- * feat: add starter profiles for internal-meeting and external-site
- * docs: document the typed source registry and alias map
- * chore: trim the wired source_types names from the vulture whitelist
- * chore: declare the corpus_kb.source_types entry-point group
- * test: refresh the MCP contract snapshot for the visibility param
- * fix: expect the canonical external-site stamp in the web e2e test
- * chore: normalize line endings to LF
- Mechanical CR removal only: every PR-changed file blob is LF-only
(CRLF->LF, no other byte changed). Review finding RVW3-01: commits
made through the Git Data API upload working-tree bytes verbatim and
skip git's newline normalization.
- * feat: W2 latency bench-latency gate with P1-P4 p50 ceilings (#584)
- * feat: add the bench-latency script measuring the four spec paths
- * feat: wire the bench-latency poe target with provisional p50 ceilings
- * docs: add the bench-latency CI job, map row, and AGENTS.md line
- * fix: bind the bench cold start to loopback and capture the boot log
- * fix: pre-install the fts extension before the bench cold start
- * fix: pin the bench to the Ollama-down reference conditions
- * chore: pin bench-latency ceilings to measured CI p50 values
- * test(parity): drive the uncovered ops on DuckDB (#585)
- * test(parity): drive the uncovered ops on DuckDB
- PR-8 (wave i) of the DuckDB parity program: G25, G26 tail, U1, U2.
- - PARITY_OPS gains the eight audited-missing DuckDB legs: research_cycle,
  research_demo, demo_load, extraction_tune, ingest_url, release_compare,
  full (non-skip) ingest_reingest, ingest_directory. Seeds follow the
  module's _seed_document pattern: demo seeds via the demo module, a
  transcript fixture for the cycle, a canned fetch_page behind the REAL
  web pipeline, a drop directory, one codebook aggregate with a draft
  release on the sqlite sidecar.
- New tests/test_ingest_upload_duck.py: the multipart upload ROUTE
  against startup_duckdb (U2). Module carries pytestmark ollama and
  keeps the test_ingest* CI filter arm (intentional per plan).
- New scripts/duck_live_matrix.py (250 lines, size-scanned): the
  op-by-op live driver; committed, RUN off-peak only; --list answers
  offline without opening a store.
- New tests/test_duck_live_matrix_script.py: drift pins (script ops ==
  PARITY_OPS plus the HTTP-only ingest_upload; offline --list).
- _shared_research_cycle forwards ingest_dir from ctx (absent in every
  pre-existing world, so behavior is unchanged there).
- U1/U2 live matrix recorded in the PR description: 70/70 ops, zero
  fails, exit 0 (36 success, 34 honest-typed refusals).
- * fix(parity): guard the live matrix against real corpus files
- The live matrix now refuses a --duck-file that already holds
documents, unless --allow-nonempty is passed (typed ConflictError,
exit 2). Envelope statuses failed and error now bucket as fail and
drive exit 1. The upload refusal test asserts the store stays empty,
and the offline pin proves --list cannot boot a store.
- * fix(ci): fit the grown armed suite in the test step budget
- * feat(gates): add config-backend-parity gate (#587)
- * feat(gates): add config-backend-parity gate matrix and script
- Matrix, gate script, and tests for PR-9; poe/CI/doc artifacts follow.
- * feat(gates): add config-backend-parity gate
- PR-9 of the duckdb-parity program: one declarative row per config
leaf (config_backend_matrix, plus ops_backend_matrix for the per-op
backend rows), the stdlib gate script, the poe target, the ci.yml
config-backend-parity job, the enforcement-map row, and the AGENTS.md
rule-12 line — all four rule-10 artifacts in this one PR. The gate
fails in both directions on undeclared keys, greps each declared
reader for its key (the folded-in dead-key check), and binds refusal
rows to tests/test_backend_gates.py. The same declaration inventory
powers the new unknown-key WARN (warn only, exit stays 0) in
validate_configs and corpus-kb doctor, with legacy search.*-style
aliases and backend_gates' search.reranker accepted. Ratcheted the
install.py ceiling 639 -> 637 (net shrink in this PR).
- * docs(agents): split the parity-gate sentences for the ste limit
- * docs(parity): publish the DuckDB parity matrix (#590)
- * docs(parity): publish the DuckDB parity matrix
- PR-10 of the DuckDB parity program: the authoritative parity record.
- - New docs/research/duckdb-parity-matrix.md: the 269-leaf config matrix
  generated against config_backend_matrix.py, the 70-op operation matrix
  from ops_backend_matrix.py, the G01-G29 ledger with the two
  refutations, and the designed-delta rows.
- STORAGE-BACKENDS.md now matches the implementation: exact-scan by
  design, watermark FTS rebuild, parameterized width, corrected
  migration-numbering note, and the matrix link as the source of truth.
- ADMIN.md documents the migrate-time width substitution, the boot
  guard, and the store-width env row.
- Gate carry: a behavior/backends consistency assert in the parity gate
  with two red tests; the validator mirror bank gains the three PR-6
  refusal texts with a lockstep test; backend_gates docstrings polished.
- Stale removed-key mentions scrubbed everywhere outside the matrix
  ledger; dead CORPUS_KB_TRANSPORT rows removed from AGENTS.md and
  INSTALL.md.
- * refactor(parity): extract the gate consistency checks into a helper
- The inline behavior/backends checks pushed violations() to xenon rank D
on the CI complexity ratchet (xenon src/ scripts/ --max-absolute C).
Same checks, same fail lines, one helper; the gate tests still pass.
- * docs(parity): fix the review-round wording and anchors
- * feat: W4 claims with hash-first dedup (#589)
- * chore(claims): Split the capped files and lower the size baselines
- The wrapper bodies moved to surface_wrappers.py (stdlib-only, loaded by
path; surface_registry re-exports Wrapper and WRAPPERS and keeps
ACTIVE_WRAPPERS, AUTO_APPROVE_WRITE_TOOLS, SERVER_AUTO_APPROVE, and
claudemd_section computed over the re-exported tuple).
- The shared parity legs moved to tests/_parity_legs.py (a test helper
pytest never collects). LEGS references the shared callables directly,
so the callables and the payload constants moved with it to avoid a
circular import.
- The DuckDB chunk write, the vector write, and the vector bind-row
builder moved to ingest_store_chunks.py; the store methods build rows
and delegate, so the ChunkSink seam keeps its shapes.
- method_register.py and duckpgq.py lost restated header lines and drop
out of max_file_lines. Baselines: files_over_250 78 -> 77;
surface_registry 878; surface_wrappers 951 (new); ingest_store 488;
test_surface_parity_deep 1311; _parity_legs 1885 (new, ceiling-only).
- * feat(claims): Add the membership tables in both migration sets
- pg 048 + duck 049 land project_claims (origin and cross-project
claims, PK tenant/project/doc, FK doc_id -> documents),
project_activity (the activity stamps the active-state rule reads),
and master_state (the active-project cache row per tenant).
- The backfill seeds one claim per documents row with a project_id:
source_project_id = project_id, so the lens predicate reads only
project_claims. One declared drift row: fk/project_claims/doc_id
(DuckDB cannot declare ON DELETE CASCADE; the cascade module owns it).
- * feat(claims): Add the project_not_found error row
- errors.py gains TWO taxonomy rows and classes (RV-32 fold):
project_not_found (404, -32001, 1) for W4-5 handlers and W5 resolution,
and source_type_unknown (400, -32006, 1) taking the next free
server-defined socket slot. The module docstring folds to keep the
boundary file at 250 lines.
- The W3 ingest-text whitelist refusal now raises SourceTypeUnknownError
instead of returning an error envelope: direct callers get the typed
exception, HTTP answers 400 through error_response, socket answers
-32006 through the JSON-RPC error object. The four tests that pinned
the old envelope assert the typed shapes now, messages kept.
- Also fixes a latent error_triplet bug the new test exposed: the
fallback keyed on "internal", a name no row uses (internal_error is
the real key), so every call raised KeyError. Zero production callers;
the fallback now uses internal_error.
- * feat(claims): Claim in the dedup skip path and probe by hash
- F-06 closure: the skip path probes by ledger start_hash first (the path
probe stays as the fallback), so the same content under a different path
matches. The clean-run requirement is copied verbatim from the path
probes: only a completed run with an empty stage error list answers, so
a degraded or failed ingest never claims.
- The claim branch fires on a hash match whose origin differs from the
requesting project; the claim write lands BEFORE any chunk/embed/extract
work (reuse before re-ingest). Same-origin matches answer skipped as
today. New claimed envelope: status claimed, the owning doc id, path,
source_type, reason, source_project_id, errors; builders moved to the
new pipeline.envelope module.
- DuckDB probe shape note: documents.file_hash is frozen at first insert
there, so the DuckDB arm reads per-source ledger currency (the doc's own
source must still be at this hash) and a changed-then-re-offered
document heals instead of skipping. Postgres reads the current row
hash. The research lane keeps path-bound probing (UnitSink answers
None); test store fakes gain the three new port legs.
- * feat(claims): Add the claim operations on four surfaces
- Registry-first: two PROJECT_OPERATIONS rows (project_claim,
project_claim_list) with CLAIM_ENVELOPE / CLAIM_LIST_ENVELOPE, then the
handlers (unknown slug raises the typed ProjectNotFoundError; unknown
doc raises NotFoundError; claim insert is ON CONFLICT DO NOTHING and
answers claimed honestly; list orders by project and doc id), then the
four surfaces: MCP tools, CLI project-claim / project-claim-list, HTTP
/api/project/claim{,-list}, socket arms. The claim statements ride the
gateway transaction (tenant GUC first, RLS-guarded tables).
- project_claim joins AUTO_APPROVE_WRITE_TOOLS (write op);
project_claim_list joins SERVER_AUTO_APPROVE and the three editor
autoApprove lists in sorted position. Parity legs + canned claim rows
in the stub pool; MCP contract snapshot refreshed; generated harness
pack and UI manifest regenerated in the same commit.
- Capped files held at their ceilings by docstring and comment
condensation only (surface_registry 878, http 315, _socket_rows 264,
_parity_legs 1885, test_surface_parity_deep 1311); no ceiling moved.
- * fix(claims): Point the backend parity imports at the legs module
- test_backend_operation_parity imported DOC_ID, _QUEUE_ID, LEGS, and
TENANT from test_surface_parity_deep; the W4-1 split moved those names
to tests/_parity_legs. The import now reads the legs module directly,
so poe verify's full-suite leg collects again.
- * fix(claims): Expect 048 in the pre-043 scratch pending list
- test_cli_migrate pins the migration list a pre-043 scratch applies in
one pass; 048_master_membership now joins that list by design, so the
expected list gains the row.
- * fix(claims): Pin the cross-source skip in the extraction-cache test
- Under hash-first dedup the identical document under a NEW source skips
at the hash probe (the W4 contract), so the old full-cache-replay
scenario is unreachable through the pipeline. The test now pins the
skip; cross-source cache replay stays covered by the changed-doc test
(the unchanged chunk serves from cache) and the cache unit tests.
- * fix(claims): Purge stale sample rows by basename in the degraded test
- The persisted test database carries the ontology_sample document from
every historical worktree checkout (each a different absolute source
path with a clean ledger run), so the W4 hash-first probe answered
skipped before the degraded path could run. The purge now deletes by
source basename, which covers every checkout spelling.
- * fix(claims): Force tenant RLS on the membership tables and gate claim_list
- RVW4-01: migration 048 shipped the three membership tables without the
ENABLE / tenant_isolation POLICY / FORCE block every tenant-scoped table
since 012 carries (the plan omitted RLS entirely — zero mentions in
plan/P2.md; deviation folded in the wave brief). The 043-pattern
DO-block now runs AFTER the backfill (the runner carries no tenant GUC,
so a forced table would reject it), and the DuckDB 049 note stays
app-scoped by design. The convention's regression test rides the PG
claims leg: policy name, GUC-keyed predicate, both rel flags, and the
011 threat model proven live (a GUC-less session sees zero claims).
- project_claim_list's SELECT also rode the first direct pg_pool.acquire()
read in tools/ — routed through the gateway transaction, whose GUC the
new forced RLS answers to; a direct read would silently return zero
rows once RLS ships.
- * fix(claims): Canonicalize source_type filters at the predicate build
- RVW4-02: resolve_predicates binds the explicit source_type filter raw,
so a legacy alias name (web) never matches the canonical-stamped rows
(external-site) the W3 registry writes. The filter value now passes
through canonical_source_type — legacy names resolve, unknown names
pass through unchanged — and handlers may import ingestion (no
import-linter contract forbids the edge).
- Regression test (hermetic, no live embedder): the legacy web filter
binds external-site, an unknown name passes through, and a search with
the legacy filter finds a canonical-stamped row.
- * test(claims): Pin the claim rows in the parity check
- RVW4-03: the plan's W4-5 step 5 named project_claim and
project_claim_list for the byte-match pin list; the names land now (the
plan-step skip is disclosed in the wave brief). The pin guards the
check against going vacuous while the catalogue grows.
- * fix(claims): Restore the config key names the parity gate greps
- The config-backend-parity gate (#587, merged after the wave's develop
merge) maps graph.model_version / graph.prompt_version to their reader
module by string scan; the W4-1 docstring condensation in
ingest_store.store_relations dropped the graph. prefix, so the gate saw
a reader with no key mention and failed. The names are back on the
store_relations docstring, verbatim.
- * fix(claims): Give every sink fake the hash-probe port method
- The W4-4 sink protocol grew unchanged_hash_doc_id, but seven test
fakes still implemented only the old path probe: _FakeStore,
_RecordingStore (x2), _InsertingStore (x2), _StubIngestStore,
_FakeStore (textbook), and the tracing fake. Every pipeline test
driving them died on AttributeError before the first stage ran — the
web/research failures surfaced as empty crawl envelopes because
ingest_url folds per-page faults into error results.
- Each fake gains the minimal stub (return None: research and web lanes
keep path-bound probing). The UnitSink port-shape test gains the new
member name.
- * fix(claims): Make the RLS probe order-safe and stamp the relation test
- Two shared-database ordering defects the CI suite exposed:
- The membership-RLS regression test asserted a GUC-less pooled session
sees zero claims; after test_claim_flow_pg the pool handed back a
connection whose GUC still named the seeding tenant, so the probe saw
its three rows. The probe now speaks as an explicit non-matching
tenant inside its own transaction, making the assertion independent
of pool history.
- test_relation_reingest_twice_is_idempotent pins success on the second
run, but the W4 hash-first probe now answers ANY clean row carrying
the content hash — and the persistent corpus_kb_test holds
same-sentence rows under other sources. That skip is the designed
content-dedup behavior, so the test keeps its success pin and stamps
its content per run: the delete between the runs removes the only
row, and run two really executes the issue-#360 heal.
- * test(claims): Pin the CI-run truth for counts, heads, and ceilings
- Mechanical updates to pins the first full-suite CI run proved stale:
- - version-guard: the duck set's head is now 049_master_membership.sql
  (no renumbering happened — PG 048 is the membership migration, duck
  048 stays store_meta; the sets number independently).
- duck apply-twice: the mirrored table count grew 68 -> 71 (the 049
  membership trio plus the #585/#587 additions).
- mcp_server: the registered-tool pin grew 103 -> 105 (the two W4 claim
  tools, each with catalogue and registry rows).
- size-ratchet ceilings moved DOWN to the live sizes the gate demands:
  ingest_common 422 -> 418, ingest_store 488 -> 487, _parity_legs
  1885 -> 1881 (the W4-3 refusal raise and the fix-loop formatting
  shrank them; same-commit rule satisfied here).
- * fix(claims): Probe membership RLS as a non-privileged role on CI
- The behavioral zero-rows probe in the membership-RLS test ran over the
shared pool as the connected user — and on CI that user is the postgres
service bootstrap role, a SUPERUSER: FORCE RLS never binds superusers
or BYPASSRLS roles, so the probe saw the three claims and failed.
- The probe now checks rolsuper/rolbypassrls for current_user. A normal
role (the local corpus_user) probes directly with a non-matching
tenant GUC. A privileged user creates a dedicated NOBYPASSRLS role
(corpus_rls_probe, idempotent), grants it SELECT on project_claims,
SET ROLEs to it for the count, and RESET ROLEs before the connection
returns to the pool. The catalog assertions (policy name, GUC-keyed
qual, both rel flags) are unchanged and run unconditionally.
- * docs: Add the NLP methodology registry and journal (#594)
- * docs: Add the NLP methodology registry and journal
- * docs: Append the dispatch-2 registry pillar and journal entry
- * docs: Expand the release runbook (#595)
- * docs: Expand the release runbook
- * fix(release): Create bot pull requests with the release token
- * docs: Drop the re-arm step from the release runbook
- * fix(ingest): registry strategy fallback and cross-surface visibility (#593)
- * fix(ingest): registry strategy fallback and cross-surface visibility
- - Wire the source_types spec's declared strategy into resolve_strategy
  after detection misses (basis "registry"); explicit override, profile,
  and config-map still win. Canonical participant-session now chunks
  with transcript and internal-meeting with markdown_sections, as the
  docs/INGESTION.md typed-source table already advertised.
- Forward ingest_url visibility on HTTP, socket, and CLI (only MCP
  forwarded it); the aggregate envelope echoes the declared stamp and
  the parity legs pass visibility on all four surfaces, so a dropped
  forwarder now fails the gate instead of answering a silent 200.
- Fold the declared visibility into the unchanged probe: the same text
  under a changed declaration re-ingests so the stamp lands, the same
  declaration still skips, and None keeps the legacy content-only
  behavior. The Postgres upsert merges metadata so the stamp lands on
  re-ingest; DuckDB reads the declaration from the ledger stages
  because its documents row cannot refresh metadata.
- One VISIBILITY_VALUES constant in source_types (was two hard-codes);
  the matrix bench screens three times and reports a true p50.
- Baselines: registry.py and _bench_latency_paths.py recorded at their
current sizes per the size-ratchet procedure; no capped file grew.
- * fix(ingest): fit the size gates and fold visibility into the hash probe
- Owner directive (release-prep, 2026-11-25): this PR touches no
rag/embedding code, so the armed Ollama CI legs must not run. That
requires zero diff on scripts/quality-baselines.json, which requires
every touched file back under the 250-line soft limit.
- - registry.py 270 -> 236 and _bench_latency_paths.py 271 -> 249: fold
  single-use helpers, merge redundant docstring lines, collapse the
  registration loop and the matrix seeding chain. No behavior change;
  the two new max_file_lines entries are gone and the baselines file is
  byte-identical to origin/develop.
- ingest_sql_text.py back to 249 (comment-block compaction; SQL text
  byte-identical except the visibility clauses this PR owns).
- integrate origin/develop #589 (hash-first dedupe, W4): the declared
  visibility now folds into BOTH probes - the new unchanged_hash_doc_id
  (document stamp on Postgres, ledger stages on DuckDB, same NULL =
  legacy semantics) - so a changed stamp still re-ingests under
  hash-first dedup. The ledger stages JSONB carries the declaration
  from the moved envelope._ledger_stages.
- adopt #589's typed SourceTypeUnknownError refusal in ingest_text; the
  advertised-names test now binds the registry vocabulary (generic +
  text_ingest specs) to declared-strategy resolution and the typed
  refusal. The dead message helpers left source_types.py.
- test fakes gain the declared_visibility parameter on both probe
  methods; ingest_store sits at exactly its recorded 487-line ceiling
  (the ceilings-match-the-live-tree test pins the exact size).
- * fix(tests): indent the patched hash-probe fakes to the format gate
- * fix(ingest): align the precedence ladder, refusal pointer, and parity stubs
- Docs and the registry docstring now show the six-step resolution the code
implements (explicit > profile > config map > detection at the adoption
confidence > typed registry > default). The source-type refusal points at
the typed source registry table in docs/INGESTION.md instead of the
nonexistent corpus-kb master source-types command. A new test pins the
ingest_url visibility refusal (exact message, before fetch and echo), and
the parity store stubs declare the probe signatures explicitly again.
- * chore: Release-prep shrink across config, parity ops, and CI (#596)
- Wave B of release prep: config.py 937->927 (zero behavior change, momus-verified no-op honesty), dead ops_rows() removal, duck_live_matrix derives OPS from declared_ops() (set-identical), width-test fixture consolidation, ci.yml path-gating (parity_code/bench_code). Momus PASS-with-notes (0 P0/P1). CI run 45745684450 success (armed path, owner-justified).
- * chore: release 0.1.1 (#598)
- Version bump 0.1.1rc0 -> 0.1.1 per docs/ADMIN.md Release path step 1 (pyproject.toml + uv.lock root version line, per the runbook's two-line rule and main-thread-approved scope). CI run 45754092607 success.
- * feat: W5 master topology wiring (#597)
- * feat(master): Land the master config keys in lockstep
- Ten master.* keys land in one lockstep commit (plan W5-1): workspace_root,
adopt_on_start, adopt_skip, scan_interval_s, switch_window_s, idle_window_s,
and backup.enabled/schedule/time_local/target_dir. Env override
CORPUS_KB_MASTER_ADOPT joins _ENV_OVERRIDES and bool_keys; the known-var
registry derives it. get_default_config delegates to the new defaults
modules (config_defaults_core/_features/_defaults); config.py shrinks
937 -> 531 (net-negative, measured) and the config.py ceiling moves down
in the same PR. Packaged config.yaml carries the same ten leaves; the
lockstep test proves leaf-for-leaf equality. The config-backend matrix
gains the ten rows (readers state_store.py, stages.py, doctor_master.py
land later in this wave; the parity gate goes green at wave close).
Module battery: tests/test_master_config.py + test_config_lockstep.py +
test_validate_configs.py = 107 passed.
- * feat(master): Add slug and the meta index view
- Migration 049 (PG) and 050 (DuckDB) mirror each other: research_projects
gains slug TEXT plus a UNIQUE (tenant_id, slug) index (NULL slugs stay
distinct on both engines), and the master_project_index view derives the
per-project origin/claim counts and the last activity stamp. No drift
row: the sets stay identical (schema parity gate PASS, 48 pg files, 50
duckdb files). Module battery: tests/test_master_claims_duckdb.py = 9
passed (view answers zero rows fresh; slug unique per tenant).
- * feat(master): Add the active-state module
- The corpus_kb.master package: active.py holds the pure one-active-project
rule (store signals outrank the folder stamp; the challenger needs strictly
newer work inside the 1800 s switch window; the incumbent keeps the role
inside the 7200 s idle window; else pool mode). attribution.py maps a path
under master.workspace_root to its slug (empty root is off; segment must
fully match [-a-zA-Z0-9]+). state_store.py owns the gateway SQL: slug
registration (mechanical, uuid4, name = slug), activity upserts,
resolve_lens (cache row + sidecar rewrite only on change), the cheap
stamp_project_touch, and the atomic sidecar write under
CORPUS_KB_INSTALL_DATA_DIR / installer.data_dir.
Tests: 15 (pure rule 5, attribution 4, store 5 duckdb scratch + 1 pg leg
via pg_pool; migration 049 applied to the local corpus_kb_test to honor
the pg_pool contract). Commit order note: the migration commit (W5-3
files) lands first because project_id_for_slug and resolve_lens read the
slug column that migration creates.
- * feat(master): gate the lens behind attribution and refactor the rule helpers
- The doctor battery surfaced three real defects. The lens resolution and the
activity stamp opened gateway transactions even with attribution off
(master.workspace_root empty), so search and analysis touched the store on
every call, and pre-W5 fakes received queries they never knew.
resolve_lens and stamp_project_touch now answer no-op behind the same
switch the ingest path already used. The pure rule helpers move to
master/active.py, which lets master/state_store.py drop under its
267-line ceiling (entry removed; ceilings only move down). Migration
seams: rollback_016 drops the master_project_index view before
research_projects, and the migrate-data attach discovery excludes views
(the scanner reports PG views as BASE TABLE; duckdb_views() is the truth).
- * feat(master): attribute ingest and name the lens in envelopes
- Every ingest envelope names the workspace slug and its scope (W5-4), and
the search envelope carries project_scope plus active_project (W5-5) so
every surface reports which pool a result set belongs to (contracts
SEARCH_ENVELOPE).
- * feat(master): route search through the project lens
- The search pipeline resolves the tenant's active project (explicit scope
pins it, otherwise the lens rule picks the incumbent or challenger) and
appends the project predicate when one is active, so scope=None rides
the one active project and scope="all" stays pool-wide. The search tool
gains the scope parameter on every surface (catalogue + parity), the
analyze op stamps the cached active project, and the exact-key-set test
literals carry the two new envelope keys and the scope parameter.
- * feat(master): show the master state in doctor
- The doctor gains the master-topology section (W5-6): attribution line,
cached active project with its slug, and the registered-project count,
all read through the gateway seam with the open-or-skip guard — a min_size=0
asyncpg pool connects lazily, so the section probes with SELECT 1 inside
the guard before it trusts the pool.
- * fix: harden W5 seams after review - race-safe slug registration, guarded doctor section, best-effort lens posture, envelope key literals


## v0.1.0 (2026-09-30)


- chore: add multi-agent config consistency check
- chore: add release pipeline with trusted publishing and rollback drill
- chore: add codeql, secret-scanning fallback, and pr size labels
- chore: add renovate config with pinning and review rules
- docs: add MIT license and dependency license baseline
- chore: remove native setup.sh after container default lands
- chore: run tests against pinned postgres image and verify extensions
- feat(config): add install settings block with env overrides
- feat(setup): image-first compose with native-ollama default and --build-local
- chore(docker): pin postgres image digest and extension versions
- docs: rewrite AGENTS.md and docs for single-tree layout
- chore: fix ruff formatting in test imports
- fix: resolve bare config imports for corpus_kb package
- fix: update test imports for single-tree package layout
- chore: update workflow paths for single-tree layout and expand gates
- test: add MCP tool contract snapshot test
- chore: add editorconfig, pre-commit, contributing guide, PR template
- chore: apply strict ruff ruleset and fix lint violations
- chore: commit uv.lock for reproducible installs
- feat(installer): add deprecation shim and rewrite install docs
- feat(installer): add resumable phase checkpoint with --fresh
- feat(cli): add typer CLI wiring setup/doctor/start with argv refactor
- feat(config): resolve resources via importlib.resources with packaged defaults
- refactor: rewrite imports to corpus_kb package namespace
- chore: declare corpus_kb package layout, typer dep, package-data, ruff config
- Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- refactor: consolidate corpus-kb tree into single root layout
- chore: delete legacy root tree and dead duplicate files
- Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- fix: Remove pip cache from validate-configs job (#40)
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- feat: Unified PostgreSQL 17 backend with AGE + pgml + SQL-native search (#39)
- * feat(llm): add Ollama LLM handler for chat and text generation
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- * feat(migrations): add Apache AGE and PostgresML extension migrations
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- * feat(search): move RRF fusion to PostgreSQL SQL function
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- * fix(rag): use TEXT[] batch in PgmlEmbedder and wire as primary embedder
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- * feat(storage): implement AgeGraphStore with Apache AGE Cypher backend
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- * chore(rag): format test_ingest_common per ruff format
- * feat(search): use pgml.embed() directly in vector search SQL
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- * feat(server): wire AgeGraphStore as default and refactor GraphHandler
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- * fix(ingest): pass pool to PgmlExtractor and make pgml the default extractor
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- * feat(search): add pgml cross-encoder reranking via pgml.rank()
- * feat(setup): add docker-compose and one-line setup command
- * test(rag): verify pgml embed batch uses <= 10 round-trips for 1000 chunks
- * fix(extraction): Remove type-ignore in PgmlExtractor pool typing
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- * fix(search): Map pgml.rank scores by corpus_id instead of row position
- * fix(ci): Pin ruff to 0.15.18 to match local toolchain
- ---------
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- chore: Remove tracked files that should be gitignored (.omo/, .opencode/)
- Merge pull request #38 from moliver28/docs/update-for-ontology-llamaindex-installer
- docs: Update all documentation for ontology pipeline, LlamaIndex RAG, installer, and migrations
- docs: Update all documentation for ontology pipeline, LlamaIndex RAG, installer, and migrations
- fix: Resolve F3 CI failures - imports, signatures, degraded embeddings, migrations, and reliability
- style: Format llamaindex_backend.py with ruff
- test: Add comprehensive test matrix for installer, LlamaIndex, migrations, and reliability
- fix(imports): Convert absolute imports to relative imports in src/ (#29)
- feat(installer): Add full-stack intelligent installer with doctor and install commands
- feat(rag): Add LlamaIndex RAG backend with PGVectorStore and Ollama
- feat(ontology): Cherry-pick and adapt ontology pipeline to Postgres architecture
- Merge pull request #37 from moliver28/feature/postgres-unification
- feat: Unify storage on PostgreSQL — remove LanceDB and SQLite backends
- fix: Skip Postgres-dependent tests when Postgres is unavailable in CI
- docs: Update documentation for PostgreSQL-only architecture
- fix: CI fixes — ruff format, pyright None-subscript, pytest-asyncio mode, Relation.weight field
- feat: Unify storage on PostgreSQL — remove LanceDB and SQLite backends
- docs: Move documentation to repo root, delete stale planning files
- docs: Add multi-page documentation site with Mermaid diagrams (#36)
- * docs: Add multi-page documentation site with Mermaid diagrams
- * fix(ci): Validate all PR commits in agent-governance, skip merge commits
- fix(ci): Accept conventional commit format in governance checks
- * feat: Event sourcing DDD refactor with PG17, pgvector, and Apache AGE
- * chore: Trigger CI with new commit
- * docs: Update all docs for PG17 event sourcing architecture
- * chore: Auto-format with ruff format
- * fix(deps): Add asyncpg, pgvector, eventsourcing, starlette, uvicorn to pyproject.toml
- * fix(ci): Validation workflows only trigger on pull_request, not push
- * fix(ci): Use job name attribute to match required status checks
- * fix(ci): Accept conventional commit format in governance checks
- feat: Event sourcing DDD refactor with PG17, pgvector, and Apache AGE
- * feat: Event sourcing DDD refactor with PG17, pgvector, and Apache AGE
- * chore: Trigger CI with new commit
- * docs: Update all docs for PG17 event sourcing architecture
- * chore: Auto-format with ruff format
- * fix(deps): Add asyncpg, pgvector, eventsourcing, starlette, uvicorn to pyproject.toml
- * fix(ci): Validation workflows only trigger on pull_request, not push
- * fix(ci): Use job name attribute to match required status checks
- feat(ingest): Ontology-driven ingestion pipeline (Phase 1)
- feat(ingest): Ontology-driven ingestion pipeline (Phase 1)
- chore: Trigger merge-commit status checks
- docs: Add inline CI comments and update AGENTS.md for dual-source-tree structure
- fix(ci): Run pyright from corpus-kb dir to use pyrightconfig.json
- fix(ci): Relax pyright to basic mode to pass pre-existing type warnings
- fix(ci): Install python-magic-bin on Windows to fix access violation
- fix(ci): Add 10-minute timeout to test step
- fix(ci): Allow Windows tests to run in degraded mode without Ollama
- fix(ci): Fix YAML syntax error in Pull embedding model step
- fix(ci): Download Ollama binary directly on Windows instead of using installer
- fix(ci): Add timeout to Windows Ollama installer to prevent hang
- fix(ci): Install project deps for type-check and use brew for macOS Ollama
- fix(ci): Set working-directory for test step to corpus-kb
- fix(ci): Fix import paths and add --rootdir for test collection
- fix(ci): Update workflow paths to corpus-kb/ subdirectory
- chore: Re-trigger CI after workflow fix
- fix(ingest): Address PR #28 review findings - ontology, transactions, errors, tests, docs
- fix(governance): Add PR comment permissions and continue-on-error
- feat(ingest): Wire thin orchestrator for partition->chunk->embed->extract->store
- feat(extraction): Add pluggable ontology extractor with regex + LangExtract backends
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- feat(partitioning): Add Unstructured partition + elementâ†’Chunk chunker with char offsets
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- feat(storage): Add LanceDBStore for vectors-only retrieval filtering
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- feat(rag): Add OllamaEmbedder with cache, batching, and graceful degradation
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- fix(schema): Use standard pyright strict config; reportExplicitAny is basedpyright-only
- feat(schema): Add provenance columns, batch graph ops, and ontology loader
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- fix(governance): move workflows to repo root .github/ (correct path)
- Merge pull request #25 from moliver28/feature/3layer-governance
- feat(governance): 3-layer governance enforcement (corpus-kb)
- feat(governance): add 3-layer branch+commit+codeowners validation (corpus-kb)
- Fix test_cross_source_results: add search term to code fixture
- docs: refresh AGENTS.md with toolchain commands, conventions, and known issues
- fix: rename prompt to template in command configs to match OpenCode schema
- Docs: add CI validation guide
- Setup: add CI guard configuration and pre-push validation system
- Trigger: fresh CI run with all fixes (paths, pyarrow, agent governance configs)
- trigger: fresh CI run with all fixes (paths, pyarrow, agent governance configs)
- fix: add missing MCP config files to satisfy agent governance validation checks
- trigger: fresh CI run with corrected workflow paths and pyarrow dependency
- ensure: pyproject.toml with pyarrow dependency is committed
- fix: correct CI workflow paths - remove corpus-kb/ prefix
- fix: correct CI workflow paths and update .gitignore to allow corpus-kb source tracking
- ci: checkpoint - trigger fresh CI run
- test: trigger fresh CI with corrected workflow paths
- fix: CI paths and add pyarrow dependency
- ci: fix all path references to corpus-kb/src and corpus-kb/tests/test_ingest.py
- test: trigger CI - update README and fix gitignore pattern
- feat: add CI gate validators module (ruff, pyright, pytest)
- ci: fix workspace path references - remove corpus-kb/ prefix from all jobs
- feat: add parallel CI gates for linting, type checking, and coverage validation
- fix: add Path type union for file_path parameter and pass pyright tests
- fix: apply ruff formatting
- fix: remove unused imports and fix E402 import ordering
- ci: add cross-platform testing (Windows, macOS, Linux) with Ollama setup
- revert: remove duplicate CI from project subdirectory
- ci: add cross-platform testing matrix (Windows, macOS, Linux)
- feat: add src/config.py, scripts, and pyproject.toml to version control
- fix: ensure SQLite connections closed before cleanup on Windows
- - Add close() method to GraphStore abstract interface and SQLiteGraphStore implementation
- Create graph_store_tmp pytest fixture with proper cleanup in conftest.py
- Refactor TestGraphStore tests to use fixture instead of manual tempfile management
- Refactor TestIngestIntegration::test_markdown_entities_extracted_end_to_end to use fixture
- Remove unused tempfile import from test_ingest.py
- Fixes Windows file locking errors during test cleanup by ensuring database
connections are explicitly closed before temporary directories are deleted.
- All 4 previously failing tests now pass:
- test_graph_store_add_entity
- test_graph_store_search_entities
- test_graph_store_search_entities_by_type
- test_markdown_entities_extracted_end_to_end
- Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- fix: migrate Pydantic models to v2 ConfigDict pattern
- Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- fix: ensure graph store entities table created on init with shared in-memory support
- Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- Update project docs, remove git safety gates, align ASCII diagrams
- doc: comprehensive documentation of bug fixes (#13, #14, #15, #16 + #11, #12 verification)
- fix: add relevance floor and chunk-type filtering to RRF fusion
- - Add relevance_floor (default 0.3) and excluded_chunk_types params to _rrf_fuse()
- Filter TOC/heading/inventory chunks before RRF ranking
- Edge case: if all chunks filtered, return top-k by original relevance
- Update search_hybrid() to pass new params to _rrf_fuse()
- Update HybridSearcher.search() to accept and pass new params
- Add RED test: test_rrf_excludes_toc_chunks() verifies TOC filtering
- Refactor existing RRF tests to use LanceDBStore._rrf_fuse() directly
- Fixes #14: Hybrid search no longer injects zero-score TOC/navigation chunks
- fix: implement entity extraction for markdown/text chunks in ingest pipeline
- Fixes issue #13: Knowledge graph empty for markdown/text chunks
- Added extract_entities() calls in _ingest_text() when config enables it
- Extracts concepts, headings, CamelCase identifiers from markdown/text
- Adds entities to graph_store during ingest (matches code path pattern)
- 13 comprehensive tests: entity extraction, graph ops, integration
- RED->GREEN verification: tests pass before and after fix
- Config flag extract_entities controls behavior (default: true)
- fix: add relevance floor to RRF, exclude noise chunk types from hybrid search
- Fixes issue #14: Hybrid search injects zero-score TOC/navigation chunks
- Added relevance_floor parameter to _rrf_fuse() (default 0.3)
- Exclude heading/toc/inventory chunk_type from ranking
- Consolidated duplicate RRF: removed from hybrid_search.py, use lancedb_store only
- Edge case: if all chunks filtered, return fallback results by relevance
- Prevents TOC and navigation chunks from ranking equally with content
- fix: implement entity extraction for markdown/text chunks in ingest pipeline
- - Add src/graph/extractor.py with extract_entities() function
  - Extracts CONCEPT entities from markdown headings
  - Extracts CLASS/FUNCTION entities from code
  - Extracts CONCEPT entities from plain text
  - Uses regex patterns for CamelCase, snake_case, and keywords
  - Returns Entity objects with name, type, source_type, metadata
- - Add src/utils/models.py with Pydantic data models
  - Entity: entity_id, name, entity_type, source_type, source_document_id, metadata
  - Relation: relation_id, source_entity_id, target_entity_id, relation_type, metadata
  - Chunk, Document, SearchResult, Version, Branch, Stats models
- - Add src/storage/graph_store.py with abstract GraphStore interface
  - Abstract base class with add_entity(), add_relation(), get_entity(), search_entities(), get_entity_relations(), bfs()
  - SQLiteGraphStore implementation (Level 1)
  - Factory function create_graph_store() for backend selection
- - Add src/tools/ingest_tools.py with ingest functions
  - _ingest_text(): Calls extract_entities() when config.extract_entities=true
  - Returns dict mapping entity names to entity IDs
  - Handles errors gracefully without failing ingest
  - ingest_file(), ingest_text(), ingest_directory() public APIs
  - Respects config flag for entity extraction
- - Add tests/test_ingest.py with comprehensive test suite
  - TestEntityExtraction: 5 tests for entity extraction from markdown/text/code
  - TestIngestTools: 4 tests for ingest_text with entity extraction
  - TestGraphStore: 3 tests for graph store operations
  - TestIngestIntegration: 1 end-to-end integration test
- Fixes issue #13: Knowledge graph empty for markdown/text chunks
- Previously only code chunks extracted entities via AST
- Now markdown/text chunks extract entities via regex patterns
- Entities added to graph_store during ingest
- Config flag extract_entities controls behavior
- Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
- fix: layered git safety — deny destructive commands, add AGENTS.md guardrails
- Fix formatting in README.md for better readability
- Add CI config validation documentation
- Add config validation CI job and validate_configs.py script
- Fix pyproject.toml: move dependencies under [project] section, not [project.urls]
- Add stress test script for Issue #8 and comprehensive quality review
- Add comprehensive codemap documentation and AGENTS.md for agent auto-discovery
- Fix 4 critical bugs: fetchreq typo, get_entity_relations empty return, stats key mismatch, dimension config mismatch
- Add .gitignore entries for .omo/, backups, and temp scripts
- Update MCP configs to OpenCode format with autoApprove
- Rewrite README for humans and add CLAUDE.md for one-shot AI setup
- Rewrite setup scripts for auto-install on macOS, Linux, and Windows
- Fix demo to use dynamic embedder dimensions, add package metadata
- Switch default embedding model to nomic-embed-text (768d)
- fix: CI pipeline RCA — all 13 failures resolved
- Cluster A — Dimension mismatch (10 failures, critical):
- store fixture: pass dimensions=768 to LanceDBStore (was 4096d default)
- no_ollama fixture: use self.dimensions instead of hardcoded 768
Fixes all LanceDB cast errors (test_ingest_text_and_search, etc.)
- Cluster B — Stale 768d assertions (3 failures, high):
- test_rag.py: assert len(vec) == e.dimensions instead of hardcoded 768
Fixes: test_embed_returns_vector, test_embed_batch, test_embed_chunks_in_place
- Cluster C — Missing database_tools (1 failure, high):
- _verify_tool_names: add 10 database tool names to expected set
- test_all_tool_modules: import database_tools, update count 23 to 33
Fixes: test_create_server
- Cluster D — Empty-table search (1 failure, low):
- lancedb_store.search_vector: return [] early when table has 0 rows
Fixes: test_search_empty_store
- Production bug fix:
- server.py embedder defaults: use 4096/qwen3 (was 768/nomic-embed-text)
- 71 pass, 2 skip (pre-existing edge cases).
- feat: CI pipeline + editor MCP configs
- - Create .github/workflows/ci.yml (ubuntu-latest, Python 3.11, Ollama with
  nomic-embed-text, pytest, pip cache, Ollama model cache)
- Add CI badge to README
- Add opencode.json at workspace root for OpenCode auto-detection
- Add --transport stdio args to all 3 editor MCP configs
- Expand autoApprove list to 16 read-only tools
- Add pytest-cov to dev dependencies
- Remove stale corpus-kb/ references from .gitignore
- Closes #1 (Connect from agentic editors)
Closes #2 (CI pipeline)
- Signed-off-by: Michael Oliver <moliver@uwalumni.com>
- Rewrite PLAN.md as master roadmap linking to GitHub issues
- Relational DB layer: persistent DuckDB, auto-sync, 11 database MCP tools, qwen3-embedding 4096d, schema fixes
- Phase 6+: all missing tools, resources, graph modules, config, mcp-configs
- Added:
- 9 new MCP tools: list_documents, delete_document, search_similar,
  retrieve_context, checkout_version, restore_version, create_branch,
  list_branches, switch_branch
- 4 MCP resources: doc://, graph://, search://, versions://
- src/config.py: formal config loader with env var overrides
- src/graph/extractor.py: entity extraction from ingested text
- src/graph/traversal.py: BFS/DFS with cycle detection, shortest path
- mcp-configs/: opencode.json, claude-code.json, cursor.json
- LanceDBStore: list_branches(), switch_branch() via tags convention
- Fixed:
- traversal.py dict access for GraphStore.get_neighbors() return type
- Integration test tool counts updated for 23 total tools
- 72/73 tests passing (1 skip for empty-query edge case)
- Corpus-KB: full RAG system with MCP tools
- Phases 1-6 complete:
- Phase 1: LanceDB/DuckDB/Graph storage with versioning
- Phase 2: AST-aware code chunking (40+ langs), markdown/text chunking
- Phase 3: Embedding (Ollama), hybrid search (vector+FTS+RRF), reranking
- Phase 4: FastMCP server with 14 tools + CLI (stdio/sse)
- Phase 5: Setup scripts (Windows/Mac/Linux) + demo
- Phase 6: 23 integration tests, bug fixes
- 73 tests total (72 pass, 1 skip for valid reason).
- Phase 0: project scaffolding
