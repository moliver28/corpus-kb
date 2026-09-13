# Ontology Ingestion Pipeline

## Overview

Corpus-KB ingests files, raw text, and directories through a five-stage
pipeline: **partition**, **chunk**, **embed**, **extract**, and **store**.
Each stage is designed to degrade gracefully. If the embedding service
(Ollama) is unavailable, the pipeline continues with zero-vector fallbacks
and reports the failure in structured error output.

The default backend is the **unified PostgreSQL 17 ecosystem** (pgvector +
Apache AGE + PostgresML) with Ollama for optional local LLM inference.
All heavy operations (embeddings, reranking, RRF fusion, and NER extraction)
can run inside Postgres, while Ollama provides a CPU-only fallback path.

## Quick Start

```bash
# 1. Start the full stack
docker compose up -d

# 2. Run the guided one-line setup (dry-run first, then apply)
corpus-kb setup --dry-run
corpus-kb setup

# 3. Start the MCP server
corpus-kb
```

## Pipeline Stages

```
                    +-------------+
                    | Input File  |
                    +------+------+
                           |
                           v
                    +------+------+
  Stage 1: Partition     |  Elements   |
                    +------+------+
                           |
                           v
                    +------+------+
  Stage 2: Chunk          |   Chunks    |
                    +------+------+
                           |
                    +------+------+
  Stage 3: Embed          |  Vectors    |  (pgml / Ollama / zero-vector fallback)
                    +------+------+
                           |
                    +------+------+
  Stage 4: Extract        | Entities +  |  (PostgresML / LangExtract / Regex)
                    | Relations   |
                    +------+------+
                           |
                    +------+------+
  Stage 5: Store          | Postgres tx |  (atomic graph writes)
                    | pgvector    |  (vectors)
                    +-------------+
```

### Stage 1: Partition

The input file is partitioned into semantic elements (paragraphs, headings,
code blocks) using the `unstructured` library. Raw text input bypasses this
stage and wraps the text in a single element.

- **File**: `src/partitioning.py`
- **Config**: `chunking.code.parser: tree-sitter`

### Stage 2: Chunk

Elements are grouped into chunks respecting AST boundaries for code
(functions, classes) and heading boundaries for markdown. Each chunk carries
its `source_start_char` and `source_end_char` offsets for provenance.

- **File**: `src/chunking/unstructured_chunker.py`
- **Config**: `chunking.max_size`, `chunking.overlap`

### Stage 3: Embed

By default embeddings are generated inside Postgres via **PostgresML**
(`pgml.embed()`), sending a batch of chunk texts as a single SQL call. When
pgml is unavailable, the pipeline falls back to the configured
`fallback_provider` (Ollama by default), and ultimately to zero vectors.
The pipeline result includes `degraded: true` and an error message in the
`errors` list.

When **matryoshka two-tier retrieval** is enabled, the full embedding is
stored in `chunks_vectors.vector` and a front-sliced, L2-renormalized 1024d
projection is stored in `vector_1024`. The 1024d column gets an HNSW index
for fast candidate generation, and the query layer rescores candidates on
the exact full-dimension column.

- **Files**: `src/rag/embedder.py`, `src/rag/pgml_embedder.py`
- **Config**: `embedding.provider`, `embedding.fallback_provider`,
  `embedding.model`, `embedding.base_url`, `embedding.dimensions`,
  `search.matryoshka_enabled`, `search.matryoshka_dim`
- **Fallback**: Ollama zero vectors (768d or configured dimensions)

### Stage 4: Extract

Entities and relations are extracted from chunks using a configurable
chain. The default primary extractor is **PostgresML NER** (`pgml`), which
runs ONNX NER models inside Postgres via `pgml.transform()`. When pgml is
unavailable, the pipeline falls back to LangExtract (LLM-based,
ontology-aware), then to RegexExtractor (rule-based, ontology-agnostic
fallback). Extraction is capped at 10 relations per chunk to prevent
quadratic explosion. LangExtract offsets are validated; invalid
offsets (negative, start >= end, out of bounds) are skipped with a warning.

Every relation stores provenance metadata: the source chunk, confidence,
extractor id, model version, and prompt version. The unique key includes
`chunk_id` so the same triple asserted in two different chunks keeps both
provenance rows.

- **Files**: `src/extraction/pgml_backend.py`, `src/extraction/langextract_backend.py`, `src/extraction/regex_backend.py`
- **Config**: `graph.extractor: pgml | langextract | regex`, `graph.extract_entities: true`
- **Provenance**: migration `008_typed_relations.sql`

### Stage 5: Store

Graph writes (document, chunks, entities, relations) are wrapped in a single
Postgres transaction via `GraphStore.transaction()` for atomicity. If
extraction fails mid-pipeline, all graph writes roll back; no partial
state is left. The default graph backend is **Apache AGE** (`graph.backend:
age`), which stores entities/relations as an openCypher property graph.
When AGE is unavailable, the implementation falls back to the recursive-CTE
PostgresGraphStore.

- **Files**: `src/tools/ingest_common.py`, `src/storage/graph_store.py`, `src/storage/age_graph_store.py`
- **Transaction**: `GraphStore.transaction()` context manager

## Deduplication and Provenance

### Document-level short-circuit

Before chunking begins, the pipeline hashes the full document text with
SHA256 and stores it in `documents.file_hash`. If the same `source` path is
ingested again with an identical hash, the run returns immediately with
`status: "skipped"` and `reason: "unchanged"`. No chunks are reprocessed,
re-embedded, or re-extracted.

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

Generation follows the same deterministic fixture pattern as LangExtract:
fixtures are keyed by SHA256 of the document prefix plus chunk text and
stored in `contextual.fixture_dir`. When `contextual.live_fallback` is
`false`, missing fixtures produce an empty blurb rather than calling the
LLM, which keeps CI deterministic. Any LLM error degrades to an empty blurb
without failing ingest.

The feature is gated by `source_type`:

- `contextual.enabled: true` turns it on for every ingest.
- Otherwise it runs only for `source_type` values listed in
  `contextual.enabled_source_types` (default: `["interview", "research"]`).

At query time, the FTS expression uses
`to_tsvector('english', coalesce(context_blurb,'') || ' ' || text)`, so
chunks without a blurb still match. Migration `009_contextual_blurb.sql`
adds the `context_blurb` column and a GIN index on the contextual
expression.

- **File**: `src/rag/contextualizer.py`
- **Config block**: `contextual`

## Matryoshka Two-Tier Retrieval

When `search.matryoshka_enabled` is `true` and the embedding provider is
`ollama`, the pipeline stores both the full embedding and a 1024d
front-sliced, L2-renormalized projection. Query execution uses the 1024d
HNSW index to retrieve `k * candidate_multiplier` candidates, then rescores
the candidates with the exact full-dimension vector and returns the top
results.

Matryoshka is **off by default** (`search.matryoshka_enabled: false`). It
requires migration `010_matryoshka_1024.sql`, which adds the `vector_1024`
column and the `idx_chunks_vectors_hnsw_1024` HNSW index.

- **File**: `src/handlers/query_handler.py`
- **Config**: `search.matryoshka_enabled`, `search.matryoshka_dim`,
  `search.candidate_multiplier`

## Search Stage

After ingestion, the search pipeline performs hybrid retrieval inside
Postgres:

1. **Self-query filtering** (optional): parse the natural-language query
   into a semantic string plus structured predicates over whitelisted
   columns (`documents.source_type`, `documents.language`,
   `documents.created_at`, `chunks.chunk_type`, `chunks.source_type`, `tag`,
   and `metadata`). The predicates are bound as parameters and appended to
   both the vector and FTS SQL arms.
2. **Vector search** with pgvector (IVFFlat/HNSW index type chosen by
   `search.index_type`), using matryoshka candidate generation when enabled.
3. **Full-text search** over `coalesce(context_blurb,'') || ' ' || text`
   via Postgres `plainto_tsquery`.
4. **Reciprocal Rank Fusion (RRF)** in pure SQL using
   `corpus.rrf_fusion()` for deterministic, rank-based score combination.
5. **Optional reranking** via the configured reranker backend.

- **File**: `src/handlers/query_handler.py`
- **Config**: `search.rrf_k`, `search.expand_context`, `search.index_type`,
  `search.reranker`, `search.reranker_model`, `search.rerank`

## Reranker Configuration

Reranking happens after RRF fusion. Two selection mechanisms coexist:

- `search.reranker` chooses the legacy async reranker:
  - `none` (default): identity pass-through.
  - `pgml`: `pgml.rank()` cross-encoder reranking inside Postgres.
  - `ollama`: qwen3-reranker scoring via Ollama's generate API.
- `search.rerank` is the newer score-based pipeline block:
  - `enabled: true` builds an `OllamaReranker`.
  - `backend: fake` builds the deterministic `FakeReranker` for CI.
  - `model`, `base_url`, `batch_size`, `over_retrieve_n`, `score_floor`,
    and `calibration` tune scoring and filtering.

Score-based rerankers over-retrieve `over_retrieve_n` fused results,
score each `(query, text)` pair, apply `calibration` (currently min-max),
filter below `score_floor`, and return the top `k`. On any backend failure
reranking falls back to the pre-rerank RRF order so search never breaks.

- **File**: `src/rag/reranker.py`
- **Config**: `search.reranker`, `search.reranker_model`, and the
  `search.rerank` block

## Self-Query Filters

The self-query parser turns a natural-language question into a clean
semantic query plus structured filters. It uses Ollama's JSON-schema-
constrained chat output, then validates every predicate against a fixed
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

- **File**: `src/rag/self_query.py`
- **Config block**: `search.self_query`

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

- **File**: `src/rag/judge.py`
- **HTTP route**: `POST /api/verify`
- **Config block**: `judge`

## Adaptive Routing

The adaptive router classifies each incoming query by cosine similarity to
per-intent prototype centroids, then dispatches it to the backend best
suited to the question:

- **semantic**: hybrid vector + FTS search.
- **relational**: templated aggregate statistics (document counts, chunk
  counts by type, etc.).
- **graph**: entity/relation traversal starting from the best matching
  entity.

Prototypes are configured under `routing.prototypes` as lists of example
phrases per intent. The centroid of each intent is the mean embedding of its
prototypes. If the best cosine score is below
`routing.confidence_threshold` (default `0.35`), or if the query vector is
all zeros (embedder degraded mode), the router falls back to semantic
search. Clients use `POST /api/query`.

- **File**: `src/handlers/router_handler.py`
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

The pipeline supports three extractors via a strategy pattern:

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

## Config Keys

| Key | Default | Description |
|-----|---------|-------------|
| `graph.extractor` | `pgml` | Extractor: `pgml`, `langextract`, or `regex` |
| `graph.extract_entities` | `true` | Enable entity extraction |
| `graph.ontology_path` | `config/ontology.yaml` | Path to ontology YAML |
| `graph.backend` | `age` | Graph store backend (`age` or `postgres`) |
| `graph.model_version` | `langextract-default` | Model version recorded in relation provenance |
| `graph.prompt_version` | `v1` | Prompt version recorded in relation provenance |
| `embedding.provider` | `pgml` | Embedding provider (`pgml` or `ollama`) |
| `embedding.fallback_provider` | `ollama` | Fallback embedding provider |
| `embedding.model` | `nomic-embed-text` | Embedding model (Ollama) |
| `embedding.dimensions` | `768` | Vector dimensions |
| `embedding.base_url` | `http://localhost:11434` | Ollama API URL |
| `search.rrf_k` | `60` | RRF rank constant |
| `search.reranker` | `none` | Legacy reranker backend (`none`, `pgml`, or `ollama`) |
| `search.reranker_model` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder for pgml reranking |
| `search.rerank.enabled` | `true` | Enable the score-based reranker pipeline |
| `search.rerank.backend` | (none) | Score-based backend; `fake` for CI |
| `search.rerank.model` | `qwen3-reranker:8b` | Ollama reranker model |
| `search.rerank.base_url` | `http://localhost:11434` | Ollama API URL for reranking |
| `search.rerank.batch_size` | `16` | Number of chunks scored per Ollama batch |
| `search.rerank.over_retrieve_n` | `60` | Number of fused results to rerank |
| `search.rerank.score_floor` | `0.15` | Minimum normalized rerank score to keep |
| `search.rerank.calibration` | `minmax` | Score calibration method |
| `search.self_query.enabled` | `true` | Enable self-query filter extraction |
| `search.self_query.model` | `qwen3:4b` | Chat model for self-query parsing |
| `search.self_query.base_url` | `http://localhost:11434` | Ollama API URL for self-query |
| `search.self_query.timeout_seconds` | `8` | Timeout for self-query LLM calls |
| `search.matryoshka_enabled` | `false` | Enable two-tier 1024d ANN retrieval |
| `search.matryoshka_dim` | `1024` | Dimension of the front-sliced projection |
| `search.candidate_multiplier` | `8` | Candidate multiplier for matryoshka retrieval |
| `search.hnsw_ef_search` | `100` | HNSW ef_search parameter |
| `contextual.enabled` | `false` | Enable contextual retrieval for all source types |
| `contextual.enabled_source_types` | `["interview", "research"]` | Source types that always get contextual blurbs |
| `contextual.model` | `qwen3:4b` | Model for blurb generation |
| `contextual.base_url` | `http://localhost:11434` | Ollama API URL for blurb generation |
| `contextual.temperature` | `0.0` | Sampling temperature for blurb generation |
| `contextual.fixture_dir` | `tests/fixtures/contextual_recorded` | Recorded blurb fixtures |
| `contextual.live_fallback` | `false` | Allow live LLM calls when no fixture exists |
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

`corpus-kb setup` is a guided wrapper around the docker-compose stack:

```bash
# Preview every step without side effects
corpus-kb setup --dry-run

# Execute the full flow
corpus-kb setup
```

It will:
1. `docker compose up -d` (PostgreSQL 17 + pgvector + AGE + pgml + Ollama)
2. `pip install -e .[dev]`
3. Create the database/user and run all migrations
4. Verify AGE + pgml extensions are installed
5. Pull `nomic-embed-text` and `qwen3:4b`
6. Write/update `~/.corpus-kb/config.yaml`

The compose stack lives at the repo root (`docker-compose.yml`) and uses
`corpus_user`/`corpus_pass`/`corpus_kb` on host port `5433`, matching the
default connection string in `config.yaml` and `src/config.py`.

## Error Handling

The pipeline captures errors structurally. The `run_pipeline` result dict
includes an `errors` key, a list of strings like
`"EmbeddingError: ConnectionRefusedError: ..."`.

- **Degraded mode**: `degraded: true` with non-empty `errors` list
- **Normal mode**: `degraded: false` with empty `errors` list

## Fixture System

LangExtract fixtures are SHA256-keyed JSONL files in
`tests/fixtures/langextract_recorded/`. Each file records the LLM's
extraction output for a specific chunk text, enabling deterministic test
runs without calling the LLM API.

- **Fixture dir**: `tests/fixtures/langextract_recorded/`
- **Config**: `graph.fixture_dir` (path to fixtures)
- **Live fallback**: `graph.live_fallback: false` (use fixtures only)

Contextual retrieval uses the same fixture pattern with plain-text `.txt`
files in `contextual.fixture_dir`.
