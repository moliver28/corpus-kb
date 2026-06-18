# src/rag/

Embedding service, hybrid search orchestration, and result reranking. Three classes that form the retrieval pipeline: text → vector → search → fusion → rerank → results.

## Responsibility

Converts natural language queries into embeddings, runs dual-path retrieval (vector similarity + full-text keyword search), fuses rankings via Reciprocal Rank Fusion, and optionally reranks top candidates through an LLM. Consumed exclusively by `search_tools.py` to power all MCP search endpoints (`search`, `search_context`, `search_similar`, `retrieve_context`).

## Design

### OllamaEmbedder (`embedder.py` — 119 lines)

Thin wrapper around `ollama.embed()` with three operational concerns:

- **Batching**: `embed_batch()` splits uncached texts into chunks of `batch_size` (default 10), calls `ollama.embed(model, input=batch)` per chunk, and reassembles results in original order. `embed_chunks()` maps over `Chunk` objects, populating `chunk.vector` in-place.
- **SHA256 cache**: In-memory `dict[str, list[float]]` keyed by `hashlib.sha256(text.encode("utf-8")).hexdigest()`. LRU-style eviction: when `cache_size` (default 10,000) is reached, the entire cache is cleared. Cache hit returns immediately without Ollama round-trip.
- **Zero-vector fallback**: On any exception (Ollama down, model missing, network error), returns `[0.0] * dimensions`. Same behavior in batch mode — uncached indices that fail get zero vectors. Graceful degradation, not hard failure.
- **Model defaults**: `nomic-embed-text` at 768 dimensions. Configurable at construction. README recommends `qwen3-embedding:8b-q8_0` (4096d, MTEB #1) as upgrade path.

Public API: `embed(text) → list[float]`, `embed_batch(texts) → list[list[float]]`, `embed_chunks(chunks) → list[Chunk]`, `clear_cache()`.

### HybridSearcher (`hybrid_search.py` — 147 lines)

Orchestrates dual-path retrieval over a `LanceDBStore`:

- **Vector path**: Embeds query via injected `OllamaEmbedder`, calls `store.search_vector()` with optional `source_type` / `file_path` filters. Default `k_vector=20`.
- **FTS path**: Calls `store.search_fts()` with the raw query string and same filters. Default `k_fts=20`.
- **RRF fusion**: `_rrf_merge()` applies Reciprocal Rank Fusion: `score = 1 / (k + rank + 1)` per result per list, summed across lists. Uses `OrderedDict` keyed by `chunk_id` to deduplicate. Results present in both lists get boosted. Default `rrf_k=60` (higher = more weight to FTS).
- **Filter builder**: `_build_filters()` constructs `{"source_type": ..., "file_path": ...}` dict, returns `None` if empty — LanceDB accepts `None` as no filter.

Public API: `search(query, k, source_type, file_path) → list[SearchResult]`. Also exposes `vector_search()` and `fts_search()` independently for callers that need single-path retrieval.

### Reranker (`reranker.py` — 117 lines)

Two-mode reranker applied after RRF fusion:

- **Identity mode** (default): Pass-through. Returns `results[:k]` unchanged. Zero latency, zero cost. Appropriate when RRF already produced optimal ranking.
- **LLM mode**: Presents top `top_k` (default 5) candidates to Ollama via `ollama.generate()`. Constructs a prompt with numbered snippets (truncated to 200 chars), asks the model to return a JSON array of ranked indices. Parses response with regex `\[[\d,\s]+\]`, falls back to space-separated number parsing, then to original order. On any exception, degrades to identity.
- **Model note**: Currently hardcoded to `nomic-embed-text` for generation — a text model, not ideal. Comment suggests swapping to `llama3.2` if available. This is a known rough edge.

Public API: `rerank(query, results, k) → list[SearchResult]`.

## Flow

Query enters through `search_tools.py` → instantiates `OllamaEmbedder`, `HybridSearcher(store, embedder)`, `Reranker(mode="identity")`:

```
query string
    │
    ▼
OllamaEmbedder.embed(query)
    │  SHA256 cache check → hit: return cached vector
    │  miss: ollama.embed(model, input=query) → cache → return
    ▼
HybridSearcher.search(query)
    │
    ├─ vector_search(query_vector, k=20)
    │     └─ LanceDBStore.search_vector(vector, filters)
    │        → list[SearchResult] ranked by cosine distance
    │
    ├─ fts_search(query, k=20)
    │     └─ LanceDBStore.search_fts(query, filters)
    │        → list[SearchResult] ranked by BM25
    │
    └─ _rrf_merge(vector_results, fts_results, k=60)
         → OrderedDict by chunk_id, score = Σ 1/(60 + rank + 1)
         → sorted descending, truncated to k
    ▼
Reranker.rerank(query, results, k)
    │  identity mode: return results[:k]
    │  llm mode: top_k → ollama.generate() → parse order → re-sort
    ▼
list[SearchResult] → returned to MCP client
```

## Integration

### Dependencies (inbound)

- **Ollama API** (`ollama` Python package): HTTP calls to `localhost:11434`. `embedder.py` calls `ollama.embed()`, `reranker.py` calls `ollama.generate()`. Ollama must be running with the configured model pulled. No retry logic, no timeout configuration — failures fall through to zero-vector or identity degradation.
- **LanceDBStore** (`storage.lancedb_store`): `HybridSearcher` depends on `LanceDBStore.search_vector()` and `LanceDBStore.search_fts()`. Store is injected at construction, not imported directly — testable via mock.
- **SearchResult model** (`utils.models.SearchResult`): Shared data class carrying `chunk_id`, `text`, `score`, `source`, and metadata. Returned by all three classes.
- **Chunk model** (`utils.models.Chunk`): Used by `embedder.embed_chunks()` to populate vectors in-place.

### Consumers (outbound)

- **search_tools.py**: Sole consumer. Instantiates all three classes per MCP tool call (`_search` helper function). Wires `HybridSearcher(store, embedder)` and `Reranker(mode="identity")`. Currently uses identity reranker — LLM mode is available but not exercised by any MCP tool.
- **Ingestion pipeline** (indirect): `OllamaEmbedder.embed_chunks()` is called during document ingest to populate chunk vectors before LanceDB write.

### Configuration surface

All tunables are constructor parameters — no `config.yaml` reads inside `src/rag/`. Callers in `search_tools.py` hardcode defaults (`k_vector=20`, `k_fts=20`, `rrf_k=60`, `mode="identity"`). To change behavior, modify the caller or inject different values.
