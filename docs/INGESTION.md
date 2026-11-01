# Ontology Ingestion Pipeline

## Overview

## Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Pipeline Stages](#pipeline-stages)
- [Statement atoms](#statement-atoms)
- [Conflicts](#conflicts)
- [Beliefs](#beliefs)
- [Deduplication and Provenance](#deduplication-and-provenance)
- [Contextual Retrieval](#contextual-retrieval)
- [Matryoshka Two-Tier Retrieval](#matryoshka-two-tier-retrieval)
- [Search Stage](#search-stage)
- [Reranker Configuration](#reranker-configuration)
- [Self-Query Filters](#self-query-filters)
- [Judge and Answer Verification](#judge-and-answer-verification)
- [Adaptive Routing](#adaptive-routing)
- [Ontology Vocabulary](#ontology-vocabulary)
- [Extractor Seam](#extractor-seam)
- [Ingestion strategies](#ingestion-strategies)
- [Ingest from the CLI](#ingest-from-the-cli)
- [Config Keys](#config-keys)
- [One-Line Setup](#one-line-setup)
- [Error Handling](#error-handling)
- [Fixture System](#fixture-system)

Corpus-KB ingests files, raw text, and directories through a five-stage
pipeline: **partition**, **chunk**, **embed**, **extract**, and **store**.
The first two stages are owned by a pluggable **ingestion strategy**
([Ingestion strategies](#ingestion-strategies)). The built-in `default`
strategy preserves the classic unstructured-partition + chunker-dispatch
behavior. The built-in `transcript` strategy chunks turn-aligned Q+A
instead. Each downstream stage is designed to degrade gracefully. If the
embedding service (Ollama) is unavailable, the pipeline stores zero-vector
fallbacks and flags the affected chunks as **degraded**. It reports the
failure in structured error output.

The default storage backend is the **Postgres 17 ecosystem** (pgvector +
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
  Stage 4: Extract        | Entities +  |  (PostgresML / LangExtract / GLiNER / Regex)
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
[Ingestion strategies](#ingestion-strategies) for the extension guide.

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

The **default `ml` backend** (`extraction.backend: ml`) composes the ML
extraction engine. `extraction.mode` picks the ladder:

- `zero_shot` (default): GLiNER schema-conditioned NER
  (`fastino/gliner2-large-v1`, six mention-level types: person,
  organization, location, date, money, product) plus GLiNER-Relex
  zero-shot relations (`knowledgator/gliner-relex-large-v1.0`). Both
  models are ontology-conditioned at inference. No examples, no
  fine-tuning, no regex anywhere.
- `few_shot`: LangExtract with examples self-prompted from the ontology.
- `customized`: few-shot plus project exemplars mined from the stored
  high-confidence relations of this tenant.

The optional `ml` dependency extra (`pip install "corpus-kb[ml]"`)
installs both model stacks. `corpus-kb setup` pre-pulls the weights.

- A bare install degrades honestly through the extraction fallback chain
  (every fallback lands in `ExtractStats.notes`).
- The extraction cache (keyed by chunk hash, backend, model version, prompt
  version) makes re-ingest of unchanged content free.
- The span-eval harness (`src/corpus_kb/extraction/eval.py`) scores per-type
  P/R/F1 with bootstrap confidence intervals.
- The promotion gate is micro F1 >= 0.80 and >= baseline + 0.02.
- The former BERT backend was dead code: the extractor factory never routed
  to it, so it has been removed. Issue #31 is the GLiNER follow-up.

## Statement atoms

After extraction, each chunk atomizes into statement rows (migration
031). The atomizer splits sentences, classifies each one against the
claim-template catalogue, and anchors the entities whose spans fall
inside it. Rows are write-once per `(tenant, statement_hash, chunk)`.
Atomization is deterministic and token-free: no LLM call. The ingest
result reports it under the `statements` key with the stored count and
the template histogram. A transcript chunk carries its
`qa_context.exchange_id` onto every statement, so moderator questions
stay linked to participant answers at the atom level.

The templates are the paved claim shapes: `preference`, `opinion`,
`sentiment_target`, `fact_triple`, `numeric_claim`, `temporal_change`,
and `attribution`, plus a built-in `statement` fallback. A new claim
shape is a new YAML file — no code. The read operations (`statements-list`,
`statements-timeline`) are on all four surfaces.

## Conflicts

The cascade (`conflicts-scan`) pairs live statements on shared anchors
and runs three gates, cheapest first. Gate A is temporal: statements
whose truth windows do not overlap are a revision, not a clash. Gate B
checks structured swaps: opposite stance on the same anchor, or the
same template with a changed object. Gate C is one zero-shot NLI
judgment over the locked prompt; a judge outage degrades to
`uncertain`. Every verdict writes a flag row (migration 032) — nothing
is ever deleted. `conflicts-resolve` finalizes a flag; a confirmed
revision supersedes the older statement through
`statements.superseded_by`.

## Beliefs

A belief is a Beta posterior over one subject-relation-object stance
(migration 033). `beliefs-current` recomputes every belief from the
statements. Each stance sentence is one evidence item. Its weight starts
at the extraction confidence. Forgetting discounts old items. Sparse
beliefs shrink toward the tenant's stance ratio. The posterior is exact
conjugate math: no sampling, no drift. Every answer carries a credible
interval.

`beliefs-timeline` segments one belief's history into eras. It runs
Beta-Bernoulli change-point detection. "Likes ice cream, then got tired
of it" reads as two dated eras over the same evidence log.
`beliefs-predict` answers with the probability the next stance is
positive. `beliefs-compare` puts one subject's objects side by side.
Conflicted statements count half; superseded ones count a quarter —
the history stays, discounted.

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
decomposes the answer into atomic factual assertions. These are answer
units, not the ontology `Claim` type. It scores each assertion against
the cited texts as `entailed`, `contradicted`, or `unsupported`.
The final `groundedness` score is the fraction of assertions labeled
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
default; GLiNER is opt-in):

1. **PostgresML NER** (`graph.extractor: pgml`, default): ONNX NER inside
   Postgres via `pgml.transform()`. Falls back to LangExtract, then to
   RegexExtractor when pgml is unavailable.

2. **LangExtract** (`graph.extractor: langextract`): LLM-based extraction
   with ontology-aware type enforcement. Uses recorded fixtures for
   deterministic test runs. Falls back to RegexExtractor on import error
   or empty extraction.

3. **RegexExtractor** (`graph.extractor: regex`): Rule-based extraction
   using heading patterns and `camelCase` splitting. Ontology-agnostic;
   produces `CONCEPT` and `CLASS` types regardless of ontology config.

4. **GLiNER** (`graph.extractor: gliner`, opt-in): schema-conditioned
   mention-level NER (`fastino/gliner2-large-v1`) for six types (person,
   organization, location, date, money, product). No relations. Requires
   the `ner` extra and `extraction.gliner.enabled: true`; gate promotion on
   the span-eval harness (see Stage 4 above).

## Ingestion strategies

Corpus-KB ingests through ONE seam. Everything above storage consumes a
`ChunkPlan` (ordered chunk drafts + per-chunk metadata + strategy name +
detection provenance). Everything below it — embed, extract, store — is
strategy-agnostic and never changes when a new modality is added. Adding
ASR output, web crawls, slides, notebooks, or tables is one module each;
`run_pipeline` is never touched again. This section is the extension
guide. The stages above cover what happens after the strategy hands over.

### The contract

A strategy implements the `IngestionStrategy` protocol
(`src/corpus_kb/ingestion/base.py`):

```python
class IngestionStrategy(Protocol):
    name: str

    def detect(self, source_type_hint, filename, head) -> DetectResult: ...

    def build_chunks(self, raw, filename, profile) -> ChunkPlan: ...
```

- `detect()` scores how well the input (explicit hint, filename, and a text
  head) fits the strategy's modality and returns
  `DetectResult(confidence, reason)`. The reason is human-readable — it is
  printed at ingest time, e.g.
  `detected: transcript (0.92, moderator-pattern) → strategy transcript`.
- `build_chunks()` turns raw content into a `ChunkPlan`. Strategies that
  don't fit the element model (like `transcript`) bypass the `unstructured`
  partitioner entirely and build chunks directly.

Registry and discovery live in `src/corpus_kb/ingestion/registry.py`:
`register_strategy()` for in-process registration and `importlib.metadata`
entry-point discovery for packaged plugins.

### Built-ins

| Strategy | What it does |
|----------|--------------|
| `default` | `unstructured` partition plus the element-to-chunk mapper. Each chunk is one element with heading paths, line numbers, and exact char offsets. Oversized elements split on exact slices. Behavior is verbatim identical to the pre-strategy pipeline. |
| `transcript` | Turn-aligned question+answer chunks from speaker-turn transcripts. Each chunk carries `qa_context` ({question, participants, exchange_id}) and the same token guard as everything else. |
| `pptx_structure` | One chunk per slide: title breadcrumbs, tables as GitHub markdown, and speaker notes as their own chunks. Core/extended-properties metadata (author, created/modified, company, subject, slide numbers) rides on every chunk. Oversized slides split at paragraph boundaries. |
| `web_structure` | Heading-section chunks from fetched HTML (`corpus-kb ingest-url` does the fetching: robots.txt, size/content-type caps, optional same-domain crawl). Every chunk carries page metadata (OG/Twitter/JSON-LD/canonical, language) plus its heading breadcrumb; nav/header/footer chrome is excluded. |
| `survey` | CSV question/answer exports (Wave 5). Each answer is one chunk. The question text rides in chunk metadata (`question`, `row`, `answer_column`), so a retrieved answer carries its question. Detection claims a `.csv` whose header has a question-like and an answer-like column; other CSVs keep the default strategy. |
| `markdown_sections` | Raw markdown text segmented at ATX/setext heading boundaries (Wave 5). Every chunk is an exact char-span slice of the canonical text and carries the heading breadcrumb. Markdown FILES keep the default partition — detection never claims them — so only the degenerate raw-text path changes. |
| `code` (optional) | Tree-sitter AST chunking for source files (Wave 5). Each top-level definition is one chunk. Adjacent small siblings pack up to the token budget. Spans are exact char offsets. Needs `pip install 'corpus-kb[code]'`. Without the extra, code files use the default plan and the result carries a warnings entry. |
| `textbook` (optional) | Windowed parsing for large PDFs (textbook program). One Docling parse per page window, a disk cache, pypdf outline enrichment, and section titles that carry across window edges. Full parse needs the `[docling]` extra; see the section below. |

### The textbook strategy

Large textbooks are long jobs. One whole-book parse holds a worker for
hours and loses everything on a crash. The textbook strategy splits the
job. It parses a PDF in page windows, caches each parsed window, and
assembles chunks after the last window lands.

**The gate.** The strategy claims PDFs. Detection sees only the file
name and the text head. The real page-count gate runs when the
strategy builds chunks. A PDF with at least `textbook.min_pages` pages
(default 120) takes the windowed path. A shorter PDF falls back to the
default plan, and the result records why. `corpus-kb textbook scan`
runs the same signals up front, so you can see the candidates before
you enqueue anything.

**Windows.** Each parse call covers `textbook.window_pages` pages
(default 50). One window is one Docling `convert` call. Memory stays
bounded, and one book cannot hold a worker for the whole run.

**Isolation.** A scanned page can crash Docling's native parser. The
crash is a segfault, so no error handler can catch it inside the
process. With `textbook.window_subprocess: true` (the default), each
Docling window parses in a spawned child process. A crash or a timeout
kills only that child. The window falls back to pypdf, the run
continues, and the fallback text caches under the pypdf key.
`textbook.window_timeout_s` (default 3600) bounds one window.

**Scan-tier timing.** Scanned pages are slow. Docling's OCR tier takes
31 to 70 seconds per page. A 912-page scan is a job of hours. Size the
window timeout for that pace. The ingest queue drains one book at a
time, so one slow scan does not block other documents.

**The environment.** Docling and onnxruntime must not share one
environment. onnxruntime breaks Docling's OCR on scanned pages at
import or parse time. The strategy warns when it sees both installed.
Remove onnxruntime from the docling environment to fix the crashes.
The `[docling]` extra in `pyproject.toml` records the same constraint.

**The parse cache.** Each parsed window caches as JSON under
`textbook.cache_dir`. The cache key is the parser version, the file
SHA-256, and the window index.

- A retry or a resume loads its windows instead of paying the parse again.
- A changed file, a changed parser, or a corrupted record misses and
  re-parses.
- One window record runs 4-8 MB, so the directory grows as windows parse.
- `textbook.cache_max_mb` (default 2048) caps the directory in MiB. The
  next parse deletes the oldest files first until the directory fits the
  cap. Set it to `0` to disable the cap.
- To clear the cache by hand, delete the directory. That is safe at any
  time; the next run re-parses what it misses and only loses time.

**Outline enrichment.** A windowed parse sees no global structure; PDF
bookmarks are the exception. The strategy reads the pypdf outline and
uses bookmark titles on pages where Docling labels are thin.

**The seam, stated plainly.** When a window starts mid-section, the
previous window's open section title carries. A section that spans many
windows keeps its opening title. A header Docling missed inside a later
window stays unfilled. Content repeated across a window boundary dedupes
by page, first window wins.

**Degrade paths, never a crash.** Without the `[docling]` extra, the
strategy warns once and parses windows with pypdf only. Page text,
outline titles, and page metadata survive. Without the `[scan]` extra,
or below `min_pages`, it falls back to the default plan.

| Key | Default | Description |
|-----|---------|-------------|
| `textbook.min_pages` | `120` | The page-count gate for the windowed path. Whole number, 1 or more |
| `textbook.window_pages` | `50` | Pages per parse window. Whole number, 1 or more |
| `textbook.parser` | `docling` | The page parser: `docling` or `pypdf` |
| `textbook.cache_dir` | `~/.corpus-kb/textbook-cache` | The parsed-window cache root |
| `textbook.window_subprocess` | `true` | Parse each Docling window in a child process, so a crash kills only the child |
| `textbook.window_timeout_s` | `3600.0` | Seconds before a child window parse is killed and pypdf takes over. Number, 1 or more |
| `textbook.cache_max_mb` | `2048` | Window-cache size cap in MiB. The oldest files evict first. `0` disables the cap |
| `textbook.structured_output` | `false` | Keep rich page structure (heading level, page box, table markdown) in the parsed windows. Flag, true or false |

Books are long jobs, so most people run them through the ingest queue
([research/HARNESS-PLAYBOOKS.md](research/HARNESS-PLAYBOOKS.md), "Recipe:
queue the textbooks"). The queue worker drains one book at a time.

### Three extension ladders

Pick the lowest ladder that does the job.

#### Ladder 1 — config mapping (no code)

Map any source type to an existing strategy in `config.yaml`. The right
answer whenever your data matches a strategy that already exists.

```yaml
ingestion:
  default: default
  strategies:
    transcript: transcript      # built-in
    call-recording: transcript  # any source_type can reuse any strategy
    survey: survey              # shipped default (Wave 5)
```

#### Ladder 2 — in-process registration (scripts, power users)

Write a small Python script that registers your strategy before ingesting:

```python
from corpus_kb.ingestion.registry import register_strategy

register_strategy(MeetingNotesStrategy())
```

Nothing is packaged; the registration lives as long as the process.

#### Ladder 3 — entry-point plugin (packaged projects)

A downstream project declares the entry point in its own `pyproject.toml`:

```toml
[project.entry-points."corpus_kb.ingestion_strategies"]
meeting-notes = "my_pkg.ingestion:MeetingNotesStrategy"
```

Corpus-KB discovers the group at startup via `importlib.metadata` and the
strategy participates in detection and config mapping like a built-in.
Zero core edits, ever.

### Selection precedence

The strategy for an ingest is resolved once, in this order:

| Precedence | Source | Example |
|------------|--------|---------|
| 1 | explicit `--source-type` override | `corpus-kb ingest call.txt --source-type transcript` |
| 2 | `ingestion.strategies[source_type]` config map | the Ladder-1 YAML above |
| 3 | content detection | each registered strategy's `detect()` scores the input; highest confidence wins |
| 4 | `ingestion.default` | `default` |

The resolved choice (strategy, confidence, reason) is printed at ingest and
recorded in the pipeline result. Every document therefore carries its detection
provenance.

### Conformance invariants

Every registered strategy — built-in or third-party — must produce a
`ChunkPlan` that passes the conformance suite:

| Invariant | Rule |
|-----------|------|
| Non-empty | At least one chunk draft, or an explicit `empty_reason` saying why none. |
| Continuous indices | Chunk indices are `0..n-1` with no gaps. |
| Token budget | Each chunk stays within 0.8 × 2048 tokens (chars/4 heuristic, ≈ 1638 chars). |
| Serializable metadata | Per-chunk metadata must be JSON-serializable (it is stored on the chunk). |
| `qa_context` provenance | Only the `transcript` strategy may set `qa_context` on a chunk. |

The suite runs over the registry. A broken third-party strategy therefore
fails loudly at registration/ingest time instead of silently corrupting
retrieval.

### Worked example: a `meeting-notes` strategy

About forty lines, usable from Ladder 2 or 3. It chunks meeting notes at
`## Decision` / `## Action` / `## Discussion` headings and records the
section kind as metadata.

```python
# my_pkg/ingestion.py
from __future__ import annotations

import re

from corpus_kb.ingestion.base import (
    TOKEN_BUDGET_CHARS,
    ChunkPlan,
    DetectResult,
    PlannedChunk,
)

class MeetingNotesStrategy:
    """Chunk meeting notes at decision/action/discussion headings."""

    name = "meeting-notes"
    _HEADING = re.compile(r"^## (Decision|Action|Discussion)\b.*$", re.MULTILINE)

    def detect(self, source_type_hint, filename, head):
        if source_type_hint == "meeting-notes":
            return DetectResult(confidence=1.0, reason="explicit override")
        hits = len(self._HEADING.findall(head))
        return DetectResult(
            confidence=min(0.9, 0.3 * hits),
            reason=f"{hits} meeting headings in head",
        )

    def build_chunks(self, raw, filename, profile):
        marks = list(self._HEADING.finditer(raw))
        if not marks:
            return ChunkPlan(
                strategy=self.name,
                document_text=raw,
                empty_reason="no Decision/Action/Discussion headings",
            )
        bounds = marks + [None]  # None = end of document
        chunks = []
        for start, end in zip(bounds, bounds[1:]):
            text = raw[start.start(): end.start() if end else len(raw)].strip()
            # Split oversized sections so every chunk fits the token budget.
            for j in range(0, len(text), TOKEN_BUDGET_CHARS):
                chunks.append(
                    PlannedChunk(
                        text=text[j: j + TOKEN_BUDGET_CHARS],
                        index=len(chunks),
                        metadata={"section": start.group(1).lower()},
                    )
                )
        return ChunkPlan(strategy=self.name, chunks=chunks, document_text=raw)


# Ladder 2:
#   from corpus_kb.ingestion.registry import register_strategy
#   register_strategy(MeetingNotesStrategy())
#
# Ladder 3 (your package's pyproject.toml):
#   [project.entry-points."corpus_kb.ingestion_strategies"]
#   meeting-notes = "my_pkg.ingestion:MeetingNotesStrategy"
```

Notes on the example:

- `detect()` returns high confidence only on evidence. An explicit
  override short-circuits; otherwise it counts headings in the head and
  never claims certainty it doesn't have.
- `build_chunks()` returns an explicit `empty_reason` when the document
  isn't a meeting note. It keeps indices continuous across the oversized-
  section split and stores only plain JSON metadata.
- After registering, `ingestion.strategies: {meeting: meeting-notes}` in
  `config.yaml` maps files tagged `meeting` to it (Ladder 1 on top of
  Ladder 2/3).

### Guidance for plugin authors

- **Loading is error-tolerant.** A plugin that fails to import, doesn't
  expose the declared attribute, or violates a conformance invariant is
  skipped. The skip carries a warning; it never takes the server down or
  breaks the built-ins. Fix the warning; don't rely on silent tolerance.
- **Be honest in `detect()`.** Return low confidence on weak evidence. A
  greedy `detect()` steals documents from the strategy that actually fits;
  the printed detection line makes this auditable after the fact.
- **Respect the token budget.** Oversized chunks degrade embedding quality
  and violate the conformance suite. Split, don't truncate.
- **Keep metadata small and JSON-safe.** Metadata is stored per chunk;
  put provenance there, not payloads.
- **Never set `qa_context`.** It is reserved for the `transcript` strategy
  and gated by the conformance suite.
- **Test with the conformance suite.** Run your strategy through the same
  invariant checks the built-ins pass before you ship the entry point.

### Why `retrieval.qa_context_in_fts` defaults to false

Transcript chunks carry `qa_context` (the question, participants, exchange
id). Including that text in the shared full-text index is **opt-in**
(`retrieval.qa_context_in_fts: true`). The reason: Postgres `ts_rank` has no
IDF. Question phrasing repeats across nearly every exchange. So question
text in the shared FTS index systematically skews ranking toward transcript
boilerplate. Keep it off until you have evaluated retrieval quality on your
own corpus with it on. The `qa_context` columns and GIN index are available
either way for structured access.

### Pick settings with data, not guesswork

Chunking and retrieval settings interact. The calibrate sweep measures
query-time recipes on your own golden labels and writes the winner to a
lock file. The step-by-step loop is [VALIDATION.md](VALIDATION.md).

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
| `extraction.gliner.enabled` | `false` | Opt-in GLiNER mention-level NER backend (requires the `ner` extra) |
| `extraction.gliner.model` | `fastino/gliner2-large-v1` | GLiNER model id |
| `extraction.gliner.revision` | `main` | Model revision pin for reproducible GLiNER loads (pin a specific HF revision hash in production) |
| `extraction.gliner.types` | 6 mention types | Mention types GLiNER extracts (person, organization, location, date, money, product) |
| `ingestion.strategies` | `{transcript: transcript}` | `source_type` → strategy-name map (extension ladder 1; see the Ingestion strategies section) |
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
| `retrieval.qa_context_in_fts` | `false` | Include transcript `qa_context` in the FTS index (opt-in — the Ingestion strategies section explains why this is off by default) |
| `retrieval.exact_filter_selectivity_threshold` | `0.05` | Selectivity gate for the filtered exact scan |
| `retrieval.matryoshka_enabled` | `false` | Enable two-tier 1024d ANN retrieval |
| `retrieval.matryoshka_dim` | `1024` | Dimension of the front-sliced projection |
| `retrieval.candidate_multiplier` | `8` | Candidate multiplier for matryoshka retrieval |
| `retrieval.self_query.enabled` | `true` | Enable self-query filter extraction |
| `retrieval.self_query.model` | `qwen3:4b` | Chat model for self-query parsing |
| `retrieval.self_query.base_url` | `http://localhost:11434` | Ollama API URL for self-query |
| `retrieval.self_query.timeout_seconds` | `8` | Timeout for self-query LLM calls |
| `retrieval.hnsw.m` | `16` | HNSW index m (migration 025 DDL; doctor warns on drift) |
| `retrieval.hnsw.ef_construction` | `128` | HNSW index `ef_construction` (same) |
| `retrieval.hnsw.iterative_scan` | `strict_order` | Transaction-local pgvector iterative scan mode (off/relaxed_order/strict_order; needs pgvector >= 0.8) |
| `retrieval.hnsw.ef_search` | `200` | Transaction-local HNSW `ef_search` (SET LOCAL per retrieval transaction) |
| `retrieval.hnsw.max_scan_tuples` | `20000` | Transaction-local HNSW `max_scan_tuples` cap |
| `reranking.enabled` | `true` | Enable the score-based reranker pipeline |
| `reranking.backend` | (none) | Score-based backend; `fake` for CI |
| `reranking.model` | `qwen3-reranker:4b` | Ollama reranker model |
| `reranking.base_url` | `http://localhost:11434` | Ollama API URL for reranking |
| `reranking.over_retrieve_n` | `60` | Number of fused results to rerank |
| `reranking.candidates` | `100` | Cap on results entering the reranker |
| `reranking.return_top_k` | `20` | The default return k: search/query callers that omit k get this value |
| `reranking.score_floor` | `0.15` | Minimum normalized rerank score to keep |
| `reranking.calibration` | `minmax` | Score calibration method |

### `ingest.contextualize`

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

### Judge and routing

| Key | Default | Description |
|-----|---------|-------------|
| `judge.enabled` | `true` | Enable assertion-level groundedness verification |
| `judge.model` | `qwen3:4b` | Model for assertion decomposition and entailment |
| `judge.base_url` | `http://localhost:11434` | Ollama API URL for the judge |
| `judge.max_claims` | `20` | Maximum atomic assertions to evaluate (key name kept) |
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
1. `docker compose up -d` (Postgres 17 + pgvector + AGE + pgml)
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
