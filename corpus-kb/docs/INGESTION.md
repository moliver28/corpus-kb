# Ontology Ingestion Pipeline

## Overview

Corpus-KB ingests files, raw text, and directories through a five-stage
pipeline: **partition**, **chunk**, **embed**, **extract**, and **store**.
Each stage is designed to degrade gracefully --- if the embedding service
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
  Stage 3: Embed          |  Vectors    |  (Ollama / zero-vector fallback)
                    +------+------+
                           |
                    +------+------+
  Stage 4: Extract        | Entities +  |  (LangExtract or Regex fallback)
                    | Relations   |
                    +------+------+
                           |
                    +------+------+
  Stage 5: Store          | SQLite tx   |  (atomic graph writes)
                    | LanceDB     |  (vectors, outside tx)
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

- **Files**: `src/rag/embedder.py`, `src/rag/pgml_embedder.py`
- **Config**: `embedding.provider`, `embedding.fallback_provider`,
  `embedding.model`, `embedding.base_url`, `embedding.dimensions`
- **Fallback**: Ollama zero vectors (768d or configured dimensions)

### Stage 4: Extract

Entities and relations are extracted from chunks using a configurable
chain. The default primary extractor is **PostgresML NER** (`pgml`), which
runs ONNX NER models inside Postgres via `pgml.transform()`. When pgml is
unavailable, the pipeline falls back to LangExtract (LLM-based,
ontology-aware), then to RegexExtractor (rule-based, ontology-agnostic
fallback). Extraction is capped at 10 relations per chunk to prevent
quadratic explosion. LangExtract offsets are validated --- invalid
offsets (negative, start >= end, out of bounds) are skipped with a warning.

- **Files**: `src/extraction/pgml_backend.py`, `src/extraction/langextract_backend.py`, `src/extraction/regex_backend.py`
- **Config**: `graph.extractor: pgml | langextract | regex`, `graph.extract_entities: true`

### Stage 5: Store

Graph writes (document, chunks, entities, relations) are wrapped in a single
Postgres transaction via `GraphStore.transaction()` for atomicity. If
extraction fails mid-pipeline, all graph writes roll back --- no partial
state is left. The default graph backend is **Apache AGE** (`graph.backend:
age`), which stores entities/relations as an openCypher property graph.
When AGE is unavailable, the implementation falls back to the recursive-CTE
PostgresGraphStore.

- **Files**: `src/tools/ingest_common.py`, `src/storage/graph_store.py`, `src/storage/age_graph_store.py`
- **Transaction**: `GraphStore.transaction()` context manager

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
   Postgres via ``pgml.transform()``. Falls back to LangExtract, then to
   RegexExtractor when pgml is unavailable.

2. **LangExtract** (`graph.extractor: langextract`): LLM-based extraction
   with ontology-aware type enforcement. Uses recorded fixtures for
   deterministic test runs. Falls back to RegexExtractor on import error
   or empty extraction.

3. **RegexExtractor** (`graph.extractor: regex`): Rule-based extraction
   using heading patterns and camelCase splitting. Ontology-agnostic ---
   produces `CONCEPT` and `CLASS` types regardless of ontology config.

## Search Stage (Stage 6)

After ingestion, the search pipeline performs hybrid retrieval inside
Postgres:

1. **Vector search** with pgvector (IVFFlat/HNSW index type chosen by
   `search.index_type`).
2. **Full-text search** over chunk text via Postgres `to_tsquery`.
3. **Reciprocal Rank Fusion (RRF)** in pure SQL using
   `corpus.rrf_fusion()` for deterministic, rank-based score combination.
4. **Optional reranking** via PostgresML (`search.reranker: pgml`) using
   `pgml.rank()`. `search.reranker: none` passes results through unchanged.

- **File**: `src/handlers/query_handler.py`
- **Config**: `search.rrf_k`, `search.expand_context`, `search.index_type`,
  `search.reranker`, `search.reranker_model`

## Config Keys

| Key | Default | Description |
|-----|---------|-------------|
| `graph.extractor` | `pgml` | Extractor: `pgml`, `langextract`, or `regex` |
| `graph.extract_entities` | `true` | Enable entity extraction |
| `graph.ontology_path` | `config/ontology.yaml` | Path to ontology YAML |
| `graph.backend` | `age` | Graph store backend (`age` or `postgres`) |
| `embedding.provider` | `pgml` | Embedding provider (`pgml` or `ollama`) |
| `embedding.fallback_provider` | `ollama` | Fallback embedding provider |
| `embedding.model` | `nomic-embed-text` | Embedding model (Ollama) |
| `embedding.dimensions` | `768` | Vector dimensions |
| `embedding.base_url` | `http://localhost:11434` | Ollama API URL |
| `search.rrf_k` | `60` | RRF rank constant |
| `search.reranker` | `none` | Reranker backend (`none` or `pgml`) |
| `search.reranker_model` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder for pgml reranking |
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
includes an `errors` key --- a list of strings like
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