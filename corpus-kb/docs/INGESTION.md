# Ontology Ingestion Pipeline

## Overview

Corpus-KB ingests files, raw text, and directories through a five-stage
pipeline: **partition**, **chunk**, **embed**, **extract**, and **store**.
Each stage is designed to degrade gracefully --- if the embedding service
(Ollama) is unavailable, the pipeline continues with zero-vector fallbacks
and reports the failure in structured error output.

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

Each chunk's text is sent to Ollama for embedding. If Ollama is unavailable,
the embedder returns zero vectors (graceful degradation). The pipeline
result includes `degraded: true` and an error message in the `errors` list.

- **File**: `src/rag/embedder.py`
- **Config**: `embedding.model`, `embedding.base_url`, `embedding.dimensions`
- **Fallback**: Zero vectors (768d or configured dimensions)

### Stage 4: Extract

Entities and relations are extracted from chunks using either the
LangExtract backend (LLM-based, ontology-aware) or the RegexExtractor
(rule-based, ontology-agnostic fallback). Extraction is capped at 10
relations per chunk to prevent quadratic explosion. LangExtract offsets
are validated --- invalid offsets (negative, start >= end, out of bounds)
are skipped with a warning.

- **Files**: `src/extraction/langextract_backend.py`, `src/extraction/regex_backend.py`
- **Config**: `graph.extractor: langextract | regex`, `graph.extract_entities: true`

### Stage 5: Store

Graph writes (document, chunks, entities, relations) are wrapped in a single
SQLite transaction for atomicity. If extraction fails mid-pipeline, all
graph writes roll back --- no partial state is left. Vector writes to LanceDB
happen outside the transaction (LanceDB does not support SQLite transactions).

- **Files**: `src/tools/ingest_common.py`, `src/storage/graph_store.py`, `src/storage/lance_store.py`
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

The pipeline supports two extractors via a strategy pattern:

1. **LangExtract** (`graph.extractor: langextract`): LLM-based extraction
   with ontology-aware type enforcement. Uses recorded fixtures for
   deterministic test runs. Falls back to RegexExtractor on import error
   or empty extraction.

2. **RegexExtractor** (`graph.extractor: regex`): Rule-based extraction
   using heading patterns and camelCase splitting. Ontology-agnostic ---
   produces `CONCEPT` and `CLASS` types regardless of ontology config.

## Config Keys

| Key | Default | Description |
|-----|---------|-------------|
| `graph.extractor` | `regex` | Extractor: `langextract` or `regex` |
| `graph.extract_entities` | `true` | Enable entity extraction |
| `graph.ontology_path` | `config/ontology.yaml` | Path to ontology YAML |
| `graph.backend` | `sqlite` | Graph store backend |
| `storage.graph_db` | `./data/graph.db` | SQLite graph DB path |
| `storage.lancedb_uri` | (none) | LanceDB vector store URI |
| `embedding.model` | `nomic-embed-text` | Ollama embedding model |
| `embedding.dimensions` | `768` | Vector dimensions |
| `embedding.base_url` | `http://localhost:11434` | Ollama API URL |

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