# Corpus-KB

Local RAG system with MCP tools for AI code editors. Ingest code and documents, search semantically, run SQL queries, ask questions with citations, and manage a knowledge graph. Everything runs locally; no cloud.

## Quickstart

```bash
# 1. Install
pip install -e .

# 2. Prepare the stack (database, schema, migrations, embedding model)
corpus-kb setup

# 3. Start the server (browser UI at http://localhost:8010/app/)
corpus-kb start
```

Editor agents connect over MCP stdio:

```bash
corpus-kb --transport stdio
```

The default embedding model is `qwen3-embedding:4b` (2560d). The full novice and expert paths are in the [README](../README.md) and [docs/GETTING-STARTED.md](../docs/GETTING-STARTED.md).

The entry point is `src/corpus_kb/server_wiring.py`, module `server_wiring:main`. Config discovery: `CORPUS_KB_CONFIG` (absolute path), then `config.yaml` in `$CWD`, then the packaged default inside the package. There is no `--config` flag. Overlays: `CORPUS_KB_CONFIG_OVERLAY` holds `os.pathsep`-split YAML paths, deep-merged in order after the main file and before env overrides (issue #348).

## Server

| Transport | Command | Use Case |
|-----------|---------|----------|
| MCP stdio | `corpus-kb --transport stdio` | Editor agents (Claude Code, Cursor, Codex, OpenCode, ZCode) |
| HTTP | `corpus-kb start --transport http --port 8010` | REST API, browser UI, OpenAI-compatible `/v1` adapter |
| JSON-RPC socket | Starts with the HTTP server | Local automation (POSIX socket; Windows TCP port) |

Readiness check for a running HTTP server: `curl http://localhost:8010/api/doctor`. It runs the same probes as `corpus-kb doctor`. Exit codes and ports: [docs/OPERATIONS.md](../docs/OPERATIONS.md).

## MCP tool surface

Do not keep a hand-written tool table here; it drifts. Counted surfaces instead:

- `corpus-kb operations --indent 0` — every operation: surfaces, params, envelope keys. Runs offline.
- Tool reference table: [docs/FEATURES.md](../docs/FEATURES.md).
- HTTP routes with curl examples: [docs/API.md](../docs/API.md).
- The generated research-commands section below (SSOT: `src/corpus_kb/surface_registry.py`).

## Editor config

Ready-made configs live in [`mcp-configs/`](../mcp-configs/): `claude-code.json`, `cursor.json`, `codex.json`, `zcode.json`, plus the root [`opencode.json`](../opencode.json). All of them launch `corpus-kb --transport stdio`. `python src/corpus_kb/_setup/validate_configs.py` checks their consistency before every push.

## Configuration

The packaged default config is [`src/corpus_kb/config.yaml`](../src/corpus_kb/config.yaml); every key there has a live consumer. A root `config.yaml` overrides it. Env overrides (`CORPUS_KB_DATABASE_URL`, `CORPUS_KB_EMBEDDING_MODEL`, and friends) are listed in [docs/ADMIN.md](../docs/ADMIN.md). Headline defaults: `database.backend: postgres`; `embedding.model: qwen3-embedding:4b` at 2560d; schema width `halfvec(2560)`.

## Storage

- **Postgres (default)**: pgvector `halfvec` vectors, Apache AGE graph, row-level security. Hybrid search fuses lexical and vector results with RRF in SQL (`corpus.rrf_fusion`).
- **DuckDB**: one local file, single-writer, no Docker. Select it with `database.backend: duckdb`. Exactly one process owns the file at a time.
- There is no LanceDB. The prototype history is in [docs/FAQ.md](../docs/FAQ.md); the backend comparison is [docs/STORAGE-BACKENDS.md](../docs/STORAGE-BACKENDS.md).

## Code map

- `src/corpus_kb/ingestion/` — pluggable ingestion strategies; the one pipeline seam.
- `src/corpus_kb/tools/` — shared handlers; CLI, HTTP, socket, and MCP are thin adapters.
- `src/corpus_kb/rag/` — embedder, reranker, late chunking, SQL-side hybrid fusion.
- `src/corpus_kb/storage/` — `SqlGateway` seam (Postgres or DuckDB) and the `GraphStore` protocol.
- `src/corpus_kb/profiles/` — one authored YAML per corpus, with `validate:` and `calibrate:` blocks.

## Deployment

Long-running or multi-consumer setups run ONE server as a daemon. Every consumer connects over HTTP. Runbook and launchd installer: [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).
<!-- corpus-harness:research-commands:start -->
## Research command pack (generated — do not edit)

Every research/coding surface, generated from `src/corpus_kb/surface_registry.py`
by `scripts/gen_harness_pack.py`. Bodies live in `.claude/commands/` and
`.opencode/command/`; editing a wrapper without a registry change fails
`scripts/gen_harness_pack.py --check`.

| Harness command | CLI path | MCP tool | Purpose |
|-----------------|----------|----------|---------|
| `corpus-ingest` | `corpus-kb research ingest-transcript` | `research_ingest_transcript` | Ingest ONE transcript (txt/vtt/srt/csv/docx): turns, roles, exchanges. |
| `corpus-ingest` | `corpus-kb research ingest` | `research_ingest` | Dynamic ingest of file/dir/glob with dedup; --watch tails a drop dir. |
| `corpus-coding-run` | `corpus-kb coding run` | `coding_run` | Deductive run: score text against a fixed codebook; calibrated thresholds; claim sizes. |
| `corpus-codebook-promote` | `corpus-kb codebook promote` | `codebook_promote` | Promote a proposed code into a NEW codebook version (human gate). |
| `corpus-research-report` | `corpus-kb research report` | `research_report` | Governance report: coverage, coding overlap, agreement between coders, gate checks. |
| `corpus-review` | `corpus-kb review accept` | `review_execute` | Confirm the model's assignment for one CodingAssignment (human review). |
| `corpus-review` | `corpus-kb review override` | `review_execute` | Overrule the model's assignment for one CodingAssignment. |
| `corpus-demo` | `corpus-kb research demo` | `research_demo` | Narrated demo pipeline on the bundled demo corpus; long-running. |
| `corpus-research-cycle` | `corpus-kb research cycle` | `research_cycle` | Research cycle: full pipeline with halt gates; long-running, synchronous on every surface (promotion is human-gated). |
| `corpus-release` | `corpus-kb release assess` | `release_assess` | Compose every release gate over current evidence; WRITES NO RELEASE. |
| `corpus-release` | `corpus-kb release request` | `release_request` | Open a draft release pinned to the current codebook content hash. |
| `corpus-release` | `corpus-kb release review` | `release_review` | Review packet: evidence, failing/not_evaluable gates, waivers, next actions. |
| `corpus-release` | `corpus-kb release release` | `release_execute` | Evaluate + record gates, apply waivers, execute the release (approver). |
| `corpus-release` | `corpus-kb release status` | `release_status` | Stage, release state, blockers, sufficiency flags, the next command. |
| `corpus-release` | `corpus-kb release compare` | `release_compare` | Diff two releases' manifests (codes, definitions, methods, metrics). |
| `corpus-ingest-front-door` | `corpus-kb ingest-status` | `ingest_status` | Recent ingest_runs ledger entries + degraded-chunk counts (honest history). |
| `corpus-ingest-front-door` | `corpus-kb ingest-reembed` | `ingest_reembed` | Re-embed degraded (zero-vector) chunks once the provider is back. |
| `corpus-ingest-front-door` | `corpus-kb ingest-reingest` | `ingest_reingest` | Bulk re-embed for a model switch; dry-run plan, resume, ledger row. |
| `corpus-topics` | `corpus-kb topics-build` | `topics_build` | Cluster stored chunk embeddings into topics (offline BERTopic). |
| `corpus-topics` | `corpus-kb topics-list` | `topics_list` | List the built topics with sizes, labels, and keywords. |
| `corpus-topics` | `corpus-kb topics-trends` | `topics_trends` | Per-topic frequency series over binned document dates. |
| `corpus-labeling` | `corpus-kb labeling-build` | `labeling_build` | Score the labeling functions and write the aggregated chunk labels. |
| `corpus-labeling` | `corpus-kb labeling-list` | `labeling_list` | List the built chunk labels with probabilities. |
| `corpus-labeling` | `corpus-kb labeling-lfs` | `labeling_lfs` | List the registered labeling functions with kinds and coverage. |
| `corpus-labeling` | `corpus-kb labeling-triage` | `labeling_triage` | Rank built labels with weak support into a human review queue. |
| `corpus-labeling` | `corpus-kb labeling-gold-check` | `labeling_gold_check` | Score built labels against a gold dev set (PPI-corrected). |
| `corpus-classification` | `corpus-kb classification-build` | `classification_build` | Fit the few-shot classifier against a seed set; write predictions. |
| `corpus-classification` | `corpus-kb classification-list` | `classification_list` | List the built chunk predictions, bounded by limit. |
| `corpus-analysis` | `corpus-kb analysis-backfill-dates` | `analysis_backfill_dates` | Backfill documents.document_date from chunk metadata (idempotent). |
| `corpus-analysis` | `corpus-kb analyze-run` | `analyze_run` | Run the composed corpus analysis and write the report. |
| `corpus-analysis-select-method` | `corpus-kb analysis-select-method` | `analysis_select_method` | Rank the register's methods for a stated task (pure registry logic). |
| `corpus-analysis-route` | `corpus-kb analysis-route` | `analysis_route` | Route a plain-language question to the right analysis method. |
| `corpus-keyphrases-extract` | `corpus-kb keyphrases-extract` | `keyphrases_extract` | Rank keyphrase candidates (tfidf, embedding, or llm variant). |
| `corpus-lexical-classify` | `corpus-kb lexical-classify` | `lexical_classify` | TF-IDF + logistic labels trained on the stored weak labels. |
| `corpus-neighbors-classify` | `corpus-kb neighbors-classify` | `neighbors_classify` | kNN vote labels over the labeled chunks, neighbors listed. |
| `corpus-centroids-classify` | `corpus-kb centroids-classify` | `centroids_classify` | Nearest-centroid labels with recorded abstentions. |
| `corpus-clusters-inspect` | `corpus-kb clusters-inspect` | `clusters_inspect` | Cluster structure report: sizes, noise, exemplar chunk ids. |
| `corpus-duplicates-screen` | `corpus-kb duplicates-screen` | `duplicates_screen` | Near-duplicate candidate pairs at a cosine threshold. |
| `corpus-outliers-rank` | `corpus-kb outliers-rank` | `outliers_rank` | Chunks ranked by distance to the k-th nearest neighbor. |
| `corpus-nli-classify` | `corpus-kb nli-classify` | `nli_classify` | Zero-shot labels via entailment judgments per hypothesis. |
| `corpus-contradictions-screen` | `corpus-kb contradictions-screen` | `contradictions_screen` | Candidate pairs the NLI model marks CONTRADICT, for human review. |
| `corpus-sentiment-screen` | `corpus-kb sentiment-screen` | `sentiment_screen` | Preset lexicon sentiment screen (vote aggregation, honest note). |
| `corpus-survey-openend-codes` | `corpus-kb survey-openend-codes` | `survey_openend_codes` | Code open-ended survey answers per question (cluster, label, count). |
| `corpus-usability-severity` | `corpus-kb usability-severity` | `usability_severity` | Score findings with the fixed 0-4 severity rubric. |
| `corpus-emotion-tag` | `corpus-kb emotion-tag` | `emotion_tag` | Tag chunks with the fixed eight-emotion set via entailment. |
| `corpus-active-learn-next` | `corpus-kb active-learn-next` | `active_learn_next` | Rank stored predictions for the next human-labeling batch. |
| `corpus-coding-ops` | `corpus-kb coding embed-codebook` | `coding_embed_codebook` | Validate, embed, and load one codebook; answers its version id. |
| `corpus-coding-ops` | `corpus-kb coding pool` | `coding_pool` | Run pooling (keyword hits, similarity) and coverage reconciliation. |
| `corpus-coding-ops` | `corpus-kb coding code-batch` | `coding_code_batch` | Code a batch of (chunk, code) cells with confidence routing. |
| `corpus-coding-ops` | `corpus-kb coding reliability` | `coding_reliability` | Sample the coded corpus and compute alpha/kappa reliability. |
| `corpus-coding-ops` | `corpus-kb coding calibrate-floors` | `coding_calibrate_floors` | Calibrate one code's pool/residual floors from labeled chunks. |
| `corpus-coding-ops` | `corpus-kb coding saturation` | `coding_saturation` | Report the ISR saturation signal and the batch stop rule. |
| `corpus-coding-ops` | `corpus-kb coding status` | `coding_status` | Corpus counts, saturation ratio, coverage by disposition, routes. |
| `corpus-coding-ops` | `corpus-kb coding export` | `coding_export` | Accepted codes joined with chunk text and document source, as rows. |
| `corpus-events` | `corpus-kb events` | `events_summary` | Event counts by topic, latest notification id, checkpoints, dead letters, recent ingest runs (read-only). |
<!-- corpus-harness:research-commands:end -->
