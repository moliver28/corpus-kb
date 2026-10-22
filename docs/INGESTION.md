# Ontology Ingestion Pipeline

## Overview

Corpus-KB ingests files, raw text, and directories through a five-stage
pipeline: **partition**, **chunk**, **embed**, **extract**, and **store**.
The first two stages are owned by a pluggable **ingestion strategy**
([INGESTION-STRATEGIES.md](INGESTION-STRATEGIES.md)). The built-in `default`
strategy preserves the classic unstructured-partition + chunker-dispatch
behavior. The built-in `transcript` strategy chunks turn-aligned Q+A
instead. Each downstream stage is designed to degrade gracefully. If the
embedding service (Ollama) is unavailable, the pipeline stores zero-vector
fallbacks and flags the affected chunks as **degraded**. It reports the
failure in structured error output.

The default storage backend is the **PostgreSQL 17 ecosystem** (pgvector +
Apache AGE, with PostgresML optional). A single-user **DuckDB file engine**
is the no-Docker alternative ([STORAGE-BACKENDS.md](STORAGE-BACKENDS.md)).
Embeddings come from the configured
provider: **Ollama by default**, or **PostgresML** for in-database
embeddings. The local LLM (question answering, judging, blurbs) runs on
Ollama. All heavy operations can also run inside Postgres when the `pgml`
extension is installed.

## Quick Start

```bash
# 1. Start the stack and load the schema (dry-run first to preview)
corpus-kb setup --dry-run
corpus-kb setup

# 2. Start the HTTP server
corpus-kb start --transport http --port 8010

# 3. Ingest from the command line
corpus-kb ingest path/to/notes.md
```

`corpus-kb --transport stdio` serves the ingest tools (`ingest_text`,
`ingest_file`, `ingest_directory`, `ingest_url`, `ingest_status`,
`ingest_reembed`) over MCP, next to the HTTP routes shown above.

## Pipeline Stages

```
                    +-------------+
                    | Input File  |
                    +------+------+
                           |
                           v
                    +------+------+
  Strategy resolve       |  Strategy   |  (explicit --source-type > config map
                    +------+------+   > content detection > ingestion.default)
                           |
                           v
                    +------+------+
  Stage 1: Partition     |  Elements   |  (default strategy: unstructured)
                    +------+------+
                           |
                           v
                    +------+------+
  Stage 2: Chunk          |   Chunks    |  (default: AST/heading/semantic gaps)
                    +------+------+
                           |
                    +------+------+
  Stage 3: Embed          |  Vectors    |  (pgml / Ollama / degraded zero-vector fallback)
                    +------+------+
                           |
                    +------+------+
  Stage 4: Extract        | Entities +  |  (PostgresML / LangExtract / GLiNER2 / Regex)
                    | Relations   |
                    +------+------+
                           |
                    +------+------+
  Stage 5: Store          | Postgres tx |  (atomic graph writes + ingest_runs ledger)
                    | pgvector    |  (vectors, degraded flags)
                    +-------------+
```

### Strategy resolution

`run_pipeline` resolves the ingestion strategy once at entry, in this order:

1. An explicit `--source-type` override on the CLI/MCP/HTTP ingest calls.
2. The `ingestion.strategies[source_type]` map in `config.yaml`.
3. Content detection: every registered strategy's `detect()` scores the
   input (filename + text head); the highest-confidence match wins.
4. `ingestion.default` (built-in: `default`).

The resolution is printed at ingest, e.g.
`detected: transcript (0.92, moderator-pattern) → strategy transcript`, and
recorded in the pipeline result. See
[INGESTION-STRATEGIES.md](INGESTION-STRATEGIES.md) for the extension guide.

### Stage 1: Partition

The input file is partitioned into semantic elements (paragraphs, headings,
code blocks) using the `unstructured` library. Raw text input bypasses this
stage and wraps the text in a single element. Strategies that don't fit the
element model (e.g. `transcript`) skip this stage entirely and build chunks
directly.

- **File**: `src/corpus_kb/partitioning.py`
- **Config**: `chunking.code.parser: tree-sitter`

### Stage 2: Chunk

Elements are grouped into chunks respecting AST boundaries for code
(functions, classes) and heading boundaries for markdown. Each chunk carries
its `source_start_char` and `source_end_char` offsets for provenance.

- **File**: `src/corpus_kb/chunking/unstructured_chunker.py`
- **Config**: `chunking.max_size`, `chunking.overlap`

### Stage 3: Embed

By default embeddings come from the configured **Ollama** model
(`embedding.provider: ollama`). Set `embedding.provider: pgml` to generate
them inside Postgres with `pgml.embed()`. When the primary provider is
unreachable, the pipeline falls back to the configured
`fallback_provider`, and ultimately to zero vectors.
Zero-vector rows are flagged `degraded = true` on `chunks` and
`chunk_vectors` (migration `022_ingest_integrity.sql`) — they are never
silently mixed into vector search:

- `retrieval.exclude_degraded` (default `true`) keeps degraded rows out of the
  vector-search candidate sets. The lexical (full-text) arm still runs.
- The search envelope reports what it left out in `degraded_excluded`
  ({count, reasons}). `count` = candidate rows the filter excluded;
  `reasons` = recorded arm impairments. A legitimately empty search (no
  candidates existed) returns plain `success` with no remedy. When results
  are empty AND an impairment was recorded, the search returns status
  `degraded_empty`. The remedy: start Ollama, then run
  `corpus-kb ingest-reembed`.
- `corpus-kb ingest-reembed` re-embeds flagged chunks once the provider is
  back; repaired rows flip `degraded` back to `false`, and its summary
  reports honest scanned/fixed/failed counts.

When **matryoshka two-tier retrieval** is enabled, the full embedding is
stored in `chunks_vectors.embedding`. A front-sliced, L2-renormalized 1024d
projection is stored in `vector_1024`. The 1024d column gets an HNSW index
for fast candidate generation. The query layer rescores candidates on
the exact full-dimension column. Re-embedding overwrites stale projections
instead of keeping them.

- **Files**: `src/corpus_kb/rag/embedder.py`
- **Config**: `embedding.provider`, `embedding.fallback_provider`,
  `embedding.model`, `embedding.base_url`, `embedding.dimensions`,
  `retrieval.matryoshka_enabled`, `retrieval.matryoshka_dim`,
  `retrieval.exclude_degraded`
- **Fallback**: Ollama zero vectors flagged degraded (configured
  dimensions)

### Stage 4: Extract

Entities and relations are extracted from chunks using a configurable
chain. The default primary extractor is **PostgresML NER** (`pgml`), which
runs ONNX NER models inside Postgres via `pgml.transform()`. When pgml is
unavailable, the pipeline falls back to LangExtract (LLM-based,
ontology-aware), then to RegexExtractor (rule-based, ontology-agnostic
fallback). Extraction is capped at 10 relations per chunk to prevent
quadratic explosion. LangExtract offsets are validated; invalid
offsets (negative, start >= end, out of bounds) are skipped with a warning.

Extraction accounting is honest. Every backend reports how many candidates
it requested, kept, dropped by the cap, and dropped by confidence. The
pipeline result carries those counts instead of inflated totals. Every
entity stores provenance metadata (`extractor`, `model`, `revision`,
`confidence`), matching the relation provenance below.

An **opt-in GLiNER2 backend** (`graph.extractor: gliner`) adds
schema-conditioned NER via `fastino/gliner2-large-v1`. It covers six
mention-level types (person, organization, location, date, money, product).
The model revision is pinned from config. It produces entity spans only —
**no relation extraction**. It is disabled by default
(`extraction.gliner.enabled: false`) and requires the optional `ner`
dependency extra (`pip install "corpus-kb[ner]"`). Promote it over the
default only with eval numbers. The span-eval harness
(`src/corpus_kb/extraction/eval.py`) scores per-type P/R/F1 with bootstrap
confidence intervals. The promotion gate is micro F1 >= 0.80 and >=
baseline + 0.02. The former BERT backend was dead code: the extractor
factory never routed to it, so it has been removed. Issue #31 is the
GLiNER2 follow-up.

Every relation stores provenance metadata: the source chunk, confidence,
extractor id, model version, and prompt version. The unique key includes
`chunk_id` so the same triple asserted in two different chunks keeps both
provenance rows.

- **Files**: `src/corpus_kb/extraction/pgml_backend.py`, `src/corpus_kb/extraction/langextract_backend.py`, `src/corpus_kb/extraction/regex_backend.py`, `src/corpus_kb/extraction/gliner_backend.py`
- **Config**: `graph.extractor: pgml | langextract | gliner | regex`, `graph.extract_entities: true`, `extraction.gliner.*`
- **Provenance**: migration `008_typed_relations.sql`

### Stage 5: Store

Graph writes (document, chunks, entities, relations) are wrapped in a single
Postgres transaction via `GraphStore.transaction()` for atomicity. If
extraction fails mid-pipeline, all graph writes roll back; no partial
state is left. The default graph backend is **Apache AGE** (`graph.backend:
age`), which stores entities/relations as an openCypher property graph.
When AGE is unavailable, the implementation falls back to the recursive-CTE
PostgresGraphStore.

Every ingest run also gets a row in the `ingest_runs` ledger (migration
`022_ingest_integrity.sql`). The run is written as `running` with the
`start_hash` BEFORE any chunks are stored. It flips to `completed` with the
`finish_hash` and per-stage results only AFTER everything is stored.
Any failure marks the run `failed` with the error — nothing reports success
on a partial ingest. Concurrent ingests of the same document contend on a
Postgres advisory lock. The loser returns an honest
`skipped: concurrent-run` instead of racing the winner.

- **Files**: `src/corpus_kb/tools/ingest_common.py`, `src/corpus_kb/tools/ingest_store.py`, `src/corpus_kb/storage/graph_store.py`, `src/corpus_kb/storage/age_graph_store.py`
- **Transaction**: `GraphStore.transaction()` context manager
- **Ledger**: `ingest_runs` (migration `022_ingest_integrity.sql`)

## Deduplication and Provenance

### Document-level short-circuit

Before chunking begins, the pipeline hashes the full document text with
SHA256 and stores it in `documents.file_hash`. An identical hash AND a
cleanly completed previous run short-circuit the re-ingest. The run returns
`status: "skipped"` and `reason: "unchanged"` immediately. No chunks are
reprocessed, re-embedded, or
re-extracted. The probe checks the `ingest_runs` ledger. A previous run
that failed partway no longer masquerades as a success: the re-ingest
actually runs.

### Chunk-level content-hash dedup

Each chunk receives a `chunk_hash` over its text. Within a document,
`PostgresIngestStore.store_chunks()` compares the incoming chunk hash to the
existing row at the same `chunk_index`:

- **New chunk**: insert and mark for embedding.
- **Changed chunk** (hash differs): update text, hash, timestamp, and blurb;
clear `superseded_at` and `tombstoned_at`; mark for re-embedding.
- **Unchanged chunk**: no-op. It is neither reinserted nor re-embedded.
- **Removed chunk** (index no longer exists): existing rows with
`chunk_index >= len(new_chunks)` are tombstoned by setting
`tombstoned_at = NOW()`.

Search always filters with `tombstoned_at IS NULL AND superseded_at IS NULL`,
so old or removed chunks never surface in results. Migration
`007_chunk_dedup_provenance.sql` added `chunk_hash`, `source_timestamp`,
`superseded_at`, and `tombstoned_at` to the `chunks` table.

## Contextual Retrieval

Contextual retrieval generates a short LLM blurb for each chunk that situates
it within the parent document. The blurb is concatenated with the raw chunk
text at embedding time and indexed for full-text search.

Generation follows the same deterministic fixture pattern as LangExtract.
Fixtures are keyed by SHA256 of the prompt profile, the document prefix,
and the chunk text. They live in `ingest.contextualize.fixture_dir`. When
`ingest.contextualize.live_fallback` is
`false`, missing fixtures produce an empty blurb rather than calling the
LLM, which keeps CI deterministic. Any LLM error degrades to an empty blurb
without failing ingest.

The feature is gated by `source_type`:

- `ingest.contextualize.enabled: true` turns it on for every ingest.
- Otherwise it runs only for `source_type` values listed in
  `ingest.contextualize.enabled_source_types` (default:
  `["interview", "research"]`).

At query time, the FTS expression uses
`to_tsvector('english', coalesce(context_blurb,'') || ' ' || text)`, so
chunks without a blurb still match. Migration `009_contextual_blurb.sql`
adds the `context_blurb` column and a GIN index on the contextual
expression.

- **File**: `src/corpus_kb/rag/contextualizer.py`
- **Config block**: `ingest.contextualize`

## Matryoshka Two-Tier Retrieval

When `retrieval.matryoshka_enabled` is `true` and the embedding provider is
`ollama`, the pipeline stores both the full embedding and a 1024d
front-sliced, L2-renormalized projection. Query execution uses the 1024d
HNSW index to retrieve `k * candidate_multiplier` candidates. It rescores
the candidates with the exact full-dimension vector and returns the top
results.

Matryoshka is **off by default** (`retrieval.matryoshka_enabled: false`). It
requires migration `010_matryoshka_1024.sql`, which adds the `vector_1024`
column and the `idx_chunks_vectors_hnsw_1024` HNSW index.

- **File**: `src/corpus_kb/handlers/query_handler.py`
- **Config**: `retrieval.matryoshka_enabled`, `retrieval.matryoshka_dim`,
  `retrieval.candidate_multiplier`

## Search Stage

After ingestion, the search pipeline performs hybrid retrieval inside
Postgres:

1. **Self-query filtering** (optional): parse the natural-language query
   into a semantic string plus structured predicates over whitelisted
   columns (`documents.source_type`, `documents.language`,
   `documents.created_at`, `chunks.chunk_type`, `chunks.source_type`, `tag`,
   and `metadata`). The predicates are bound as parameters and appended to
   both the vector and FTS SQL arms.
2. **Vector search** with pgvector, using matryoshka candidate generation
   when enabled. The main column carries one HNSW index
   (`halfvec_cosine_ops`, m=16, ef_construction=128 — migration 025).
3. **Full-text search** over `coalesce(context_blurb,'') || ' ' || text`
   via Postgres `plainto_tsquery`.
4. **Reciprocal Rank Fusion (RRF)** in pure SQL using
   `corpus.rrf_fusion()` for deterministic, rank-based score combination.
5. **Optional reranking** via the configured reranker backend.

Degraded (zero-vector) chunks are excluded from the vector candidate sets
when `retrieval.exclude_degraded` is `true` (the default). The exclusion is
applied to candidates only, never to the seed documents. The envelope
reports the count and reasons in `degraded_excluded`. An all-zero query
embedding (embedder degraded mode) abstains the vector arm instead of
returning arbitrary cosine orderings.

- **File**: `src/corpus_kb/handlers/query_handler.py`
- **Config**: `retrieval.rrf_k`, `retrieval.vector_candidates`,
  `retrieval.lexical_candidates`, `reranking.*`,
  `retrieval.exclude_degraded`

## Reranker Configuration

Reranking happens after RRF fusion. Two selection mechanisms coexist:

- `reranking.*` is the score-based pipeline block (canonical):
  - `enabled: true` (the default) builds an `OllamaReranker`.
  - `backend: fake` builds the deterministic `FakeReranker` for CI.
  - `model`, `base_url`, `over_retrieve_n`, `score_floor`, and
    `calibration` tune scoring and filtering.
- `search.reranker` stays as the accepted legacy selector:
  - `none` (default): identity pass-through.
  - `pgml`: `pgml.rank()` cross-encoder reranking inside Postgres.
  - `ollama`: qwen3-reranker scoring via Ollama's generate API.

Score-based rerankers over-retrieve `over_retrieve_n` fused results,
score each `(query, text)` pair, apply `calibration` (currently min-max),
filter below `score_floor`, and return the top `k`. On any backend failure
reranking falls back to the pre-rerank RRF order so search never breaks.

- **File**: `src/corpus_kb/rag/reranker.py`
- **Config**: the `reranking` block; the legacy `search.reranker` and
  `search.reranker_model` selectors stay accepted

## Self-Query Filters

The self-query parser turns a natural-language question into a clean
semantic query plus structured filters. It uses Ollama's JSON-schema-
constrained chat output. It then validates every predicate against a fixed
whitelist before building a parameterized SQL fragment. No LLM-provided
value is ever interpolated into SQL text.

Supported filter targets:

- `documents.source_type`
- `documents.language`
- `documents.created_at` (`<`, `>`, `<=`, `>=`)
- `chunks.chunk_type`
- `chunks.source_type`
- `tag` (membership via `document_tags`)
- `metadata.<key>` (exact key/value match)

On parse or LLM failure the parser degrades to the original query with no
predicates.

- **File**: `src/corpus_kb/rag/self_query.py`
- **Config block**: `retrieval.self_query`

## Judge and Answer Verification

The judge verifies LLM-generated answers against cited chunks. It first
decomposes the answer into atomic factual claims, then scores each claim
against the cited texts as `entailed`, `contradicted`, or `unsupported`.
The final `groundedness` score is the fraction of claims labeled
`entailed`.

Clients call it via `POST /api/verify` with an `answer` and a list of
`chunk_ids`. If no chunks are supplied or the judge is disabled, the
endpoint abstains (`abstained: true`). Any LLM failure returns
`unsupported` at `0.0` confidence so verification never raises.

- **File**: `src/corpus_kb/rag/judge.py`
- **HTTP route**: `POST /api/verify`
- **Config block**: `judge`

## Adaptive Routing

The adaptive router classifies each incoming query by cosine similarity to
per-intent prototype centroids. It then dispatches the query to the backend
best suited to the question:

- **semantic**: hybrid vector + FTS search.
- **relational**: templated aggregate statistics (document counts, chunk
  counts by type, etc.).
- **graph**: entity/relation traversal starting from the best matching
  entity.

Prototypes are configured under `routing.prototypes` as lists of example
phrases per intent. The centroid of each intent is the mean embedding of its
prototypes. If the best cosine score is below
`routing.confidence_threshold` (default `0.35`), the router falls back to
semantic search. The same fallback applies when the query vector is all
zeros (embedder degraded mode). Clients use `POST /api/query`.

- **File**: `src/corpus_kb/handlers/router_handler.py`
- **HTTP route**: `POST /api/query`
- **Config block**: `routing`

## Ontology Vocabulary

The ontology constrains entity and relation types to a fixed vocabulary.
This ensures consistency across extractors and enables type-safe graph
queries.

### Entity Types (9)

| Type | Description |
|------|-------------|
| Document | A document or file |
| Section | A section within a document |
| Chunk | A chunk of text |
| Person | A person mentioned in the text |
| Org | An organization |
| Product | A product or tool |
| Concept | An abstract concept or idea |
| Claim | A claim or assertion |
| Metric | A quantitative metric |

### Relation Types (9)

| Type | Description |
|------|-------------|
| PART_OF | Source is part of target |
| MENTIONS | Source mentions target |
| DEFINED_AS | Source defines target |
| AUTHORED_BY | Source is authored by target |
| CITES | Source cites target |
| SUPPORTS | Source supports target |
| CONTRADICTS | Source contradicts target |
| RELATED_TO | Source is related to target |
| INSTANCE_OF | Source is an instance of target |

### Configuration

The ontology path is configurable via `config.yaml`:

```yaml
graph:
  ontology_path: config/ontology.yaml
```

If not set, defaults to `config/ontology.yaml`.

## Extractor Seam

The pipeline supports four extractors via a strategy pattern (three ship by
default; GLiNER2 is opt-in):

1. **PostgresML NER** (`graph.extractor: pgml`, default): ONNX NER inside
   Postgres via `pgml.transform()`. Falls back to LangExtract, then to
   RegexExtractor when pgml is unavailable.

2. **LangExtract** (`graph.extractor: langextract`): LLM-based extraction
   with ontology-aware type enforcement. Uses recorded fixtures for
   deterministic test runs. Falls back to RegexExtractor on import error
   or empty extraction.

3. **RegexExtractor** (`graph.extractor: regex`): Rule-based extraction
   using heading patterns and camelCase splitting. Ontology-agnostic;
   produces `CONCEPT` and `CLASS` types regardless of ontology config.

4. **GLiNER2** (`graph.extractor: gliner`, opt-in): schema-conditioned
   mention-level NER (`fastino/gliner2-large-v1`) for six types (person,
   organization, location, date, money, product). No relations. Requires
   the `ner` extra and `extraction.gliner.enabled: true`; gate promotion on
   the span-eval harness (see Stage 4 above).

## Ingest from the CLI

`corpus-kb ingest` is the top-level front door for files and directories:

```bash
# one file or a whole directory
corpus-kb ingest path/to/file.md
corpus-kb ingest docs/

# override strategy detection
corpus-kb ingest call.txt --source-type transcript

# watch a drop folder; auto-heal failed ingests when Ollama returns
corpus-kb ingest interviews/ --watch

# status and re-embed surfaces
corpus-kb ingest-status
corpus-kb ingest-reembed [--limit N] [--doc-id ID]
```

Flags: `--source-type` (explicit strategy override), `--watch` (directory
poll), `--reembed`, `--limit`, `--doc-id`, `--json` (machine-readable
output), `--no-heal` (disable watch auto-heal).

**Exit codes**: `0` everything ingested, `1` partial success (some files
failed), `2` total failure, `3` configuration error. They are printed in
`--help` and stable for scripting.

**Watch auto-heal**: `--watch` polls the directory and records every run in
the `ingest_runs` ledger. When a run fails because embeddings were
unavailable, the watcher re-attempts ONLY that failure class once Ollama
health returns. The mechanism: health probe + backoff + capped attempts. Other failures
are never retried silently; `--no-heal` turns auto-heal off. Ctrl-C exits
cleanly.

## Config Keys

| Key | Default | Description |
|-----|---------|-------------|
| `graph.extractor` | `pgml` | Extractor: `pgml`, `langextract`, `gliner`, or `regex` |
| `graph.extract_entities` | `true` | Enable entity extraction |
| `graph.ontology_path` | `config/ontology.yaml` | Path to ontology YAML |
| `graph.backend` | `age` | Graph store backend (`age` or `postgres`) |
| `graph.model_version` | `langextract-default` | Model version recorded in relation provenance |
| `graph.prompt_version` | `v1` | Prompt version recorded in relation provenance |
| `extraction.gliner.enabled` | `false` | Opt-in GLiNER2 mention-level NER backend (requires the `ner` extra) |
| `extraction.gliner.model` | `fastino/gliner2-large-v1` | GLiNER2 model id |
| `extraction.gliner.revision` | `main` | Model revision pin for reproducible GLiNER2 loads (pin a specific HF revision hash in production) |
| `extraction.gliner.types` | 6 mention types | Mention types GLiNER2 extracts (person, organization, location, date, money, product) |
| `ingestion.strategies` | `{transcript: transcript}` | source_type → strategy-name map (extension ladder 1; see INGESTION-STRATEGIES.md) |
| `ingestion.default` | `default` | Strategy used when no override, mapping, or detection matches |
| `embedding.provider` | `ollama` | Embedding provider (`ollama` or `pgml`) |
| `embedding.fallback_provider` | `ollama` | Fallback embedding provider |
| `embedding.model` | `qwen3-embedding:4b` | Embedding model (Ollama) |
| `embedding.dimensions` | `2560` | Vector dimensions (must equal the schema width) |
| `embedding.base_url` | `http://localhost:11434` | Ollama API URL |
| `storage.vector_type` | `halfvec` | Embedding column type (HNSW caps `vector` at 2000 dims) |
| `retrieval.mode` | `hybrid_rrf` | Retrieval strategy (single supported value today) |
| `retrieval.vector_candidates` | `150` | Live vector-arm depth (effective limit: max(candidates, k * 2)) |
| `retrieval.lexical_candidates` | `150` | Live FTS-arm depth (same floor rule) |
| `retrieval.rrf_k` | `60` | RRF rank constant |
| `retrieval.expand_to_parent` | `true` | Post-fusion small-to-big parent expansion |
| `retrieval.exclude_degraded` | `true` | Exclude degraded (zero-vector) chunks from vector-search candidates |
| `retrieval.qa_context_in_fts` | `false` | Include transcript `qa_context` in the FTS index (opt-in — see INGESTION-STRATEGIES.md for why this is off by default) |
| `retrieval.exact_filter_selectivity_threshold` | `0.05` | Selectivity gate for the filtered exact scan |
| `retrieval.matryoshka_enabled` | `false` | Enable two-tier 1024d ANN retrieval |
| `retrieval.matryoshka_dim` | `1024` | Dimension of the front-sliced projection |
| `retrieval.candidate_multiplier` | `8` | Candidate multiplier for matryoshka retrieval |
| `retrieval.self_query.enabled` | `true` | Enable self-query filter extraction |
| `retrieval.self_query.model` | `qwen3:4b` | Chat model for self-query parsing |
| `retrieval.self_query.base_url` | `http://localhost:11434` | Ollama API URL for self-query |
| `retrieval.self_query.timeout_seconds` | `8` | Timeout for self-query LLM calls |
| `retrieval.hnsw.m` | `16` | HNSW index m (migration 025 DDL; doctor warns on drift) |
| `retrieval.hnsw.ef_construction` | `128` | HNSW index ef_construction (same) |
| `retrieval.hnsw.iterative_scan` | `strict_order` | Transaction-local pgvector iterative scan mode (off/relaxed_order/strict_order; needs pgvector >= 0.8) |
| `retrieval.hnsw.ef_search` | `200` | Transaction-local HNSW ef_search (SET LOCAL per retrieval transaction) |
| `retrieval.hnsw.max_scan_tuples` | `20000` | Transaction-local HNSW max_scan_tuples cap |
| `reranking.enabled` | `true` | Enable the score-based reranker pipeline |
| `reranking.backend` | (none) | Score-based backend; `fake` for CI |
| `reranking.model` | `qwen3-reranker:4b` | Ollama reranker model |
| `reranking.base_url` | `http://localhost:11434` | Ollama API URL for reranking |
| `reranking.over_retrieve_n` | `60` | Number of fused results to rerank |
| `reranking.candidates` | `100` | Cap on results entering the reranker |
| `reranking.return_top_k` | `20` | The default return k: search/query callers that omit k get this value |
| `reranking.score_floor` | `0.15` | Minimum normalized rerank score to keep |
| `reranking.calibration` | `minmax` | Score calibration method |

#### ingest.contextualize

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable contextual retrieval for all source types |
| `enabled_source_types` | `["interview", "research"]` | Source types that always get blurbs |
| `backend` | `ollama` | Blurb generator backend; only `ollama` is supported, other values are rejected at load |
| `model` | `qwen3:8b` | Model for blurb generation; missing model or down Ollama degrades to an empty blurb |
| `prompt_profile` | `interview` | `interview` names the moderator question (transcript sources); `generic` is the neutral template |
| `max_context_tokens` | `100` | Cap on the generated blurb (chars/4 estimated tokens); truncates at a sentence or word boundary |
| `document_first_prompt` | `true` | `true` puts document context before the chunk in the prompt; `false` is the chunk-first variant |
| `apply_to` | `["embedding", "lexical"]` | Write-time blurb targets: `embedding` builds the `blurb\n\ntext` embed input, `lexical` writes the `chunks.context_blurb` FTS column |
| `base_url` | `http://localhost:11434` | Ollama API URL for blurb generation |
| `temperature` | `0.0` | Sampling temperature for blurb generation |
| `fixture_dir` | `tests/fixtures/contextual_recorded` | Recorded blurb fixtures; the cache key includes the profile |
| `live_fallback` | `false` | Allow live LLM calls when no fixture exists |

The two `apply_to` targets are independent write-time switches. With
`lexical` omitted, the column write is `NULL` (the FTS expression stays
blurb-free) while the embed input still carries the blurb. With
`embedding` omitted, chunks embed raw text while the column still serves
FTS. One trade-off: a blurb that reaches neither the column nor any
persisted text cannot be recovered later. The reembed repair path
rebuilds vectors from raw text when `lexical` is absent. Keep `lexical`
in the list (the default) when repair fidelity matters.

Legacy namespaces still load as aliases: `search.*`, `ingest.hierarchy.*`,
and `contextual.*`. Canonical keys win. Alias use logs a deprecation
warning.

#### judge + routing

| Key | Default | Description |
|-----|---------|-------------|
| `judge.enabled` | `true` | Enable claim-level groundedness verification |
| `judge.model` | `qwen3:4b` | Model for claim decomposition and entailment |
| `judge.base_url` | `http://localhost:11434` | Ollama API URL for the judge |
| `judge.max_claims` | `20` | Maximum atomic claims to evaluate |
| `routing.enabled` | `true` | Enable adaptive query routing |
| `routing.confidence_threshold` | `0.35` | Minimum cosine score to accept a non-semantic route |
| `routing.prototypes.semantic` | (list) | Example phrases for semantic search intent |
| `routing.prototypes.relational` | (list) | Example phrases for aggregate/statistics intent |
| `routing.prototypes.graph` | (list) | Example phrases for graph traversal intent |
| `llm.model` | `qwen3:4b` | Local Ollama chat/generation model |
| `llm.base_url` | `http://localhost:11434` | Ollama API URL |

## One-Line Setup

`corpus-kb setup` is a guided wrapper around the Docker Compose stack:

```bash
# Preview every step without side effects
corpus-kb setup --dry-run

# Execute the full flow
corpus-kb setup
```

It will:
1. `docker compose up -d` (PostgreSQL 17 + pgvector + AGE + pgml)
2. `pip install -e .[dev]`
3. Create the database/user and run all migrations
4. Verify AGE + pgml extensions are installed
5. Pull `nomic-embed-text` and `qwen3:4b`
6. Write/update the installer config file under `~/.corpus-kb`

The compose stack lives at the repo root (`compose.yaml`) and uses
`corpus_user`/`corpus_pass`/`corpus_kb` on host port `5433`, matching the
default connection string in `config.yaml` and `src/corpus_kb/config.py`.

## Error Handling

The pipeline captures errors structurally. The `run_pipeline` result dict
includes an `errors` key, a list of strings like
`"EmbeddingError: ConnectionRefusedError: ..."`.

- **Degraded mode**: `degraded: true` with non-empty `errors` list; the
  affected chunks and vectors are flagged `degraded = true` in Postgres, so
  the degradation outlives the response
- **Normal mode**: `degraded: false` with empty `errors` list

The result dict also names the resolved ingestion strategy (`strategy` key)
with its detection provenance. Failures mark the `ingest_runs` row `failed`
— a partially ingested document never reports `status: "success"` (see
Stage 5 above). The CLI front door maps run outcomes to exit codes. They
are 0 ok / 1 partial / 2 failed / 3 config.

## Fixture System

LangExtract fixtures are SHA256-keyed JSONL files in
`tests/fixtures/langextract_recorded/`. Each file records the LLM's
extraction output for a specific chunk text. This enables deterministic
test runs without calling the LLM API.

- **Fixture dir**: `tests/fixtures/langextract_recorded/`
- **Config**: `graph.fixture_dir` (path to fixtures)
- **Live fallback**: `graph.live_fallback: false` (use fixtures only)

Contextual retrieval uses the same fixture pattern with plain-text `.txt`
files in `contextual.fixture_dir`.
