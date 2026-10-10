# Retrieval benchmarks (P3: U22/U23/U30/U31/U45/U46)

Statuses follow the house honesty vocabulary. **Every number in this file is
a LOCAL-MACHINE measurement** (Windows x64 host, local Postgres 17 + pgvector
0.8.3 + local Ollama 0.31.1) captured on 2026-10-10 — they are harness
prove-outs and smoke signals, NOT CI truths and NOT corpus decisions. The
decision-grade run happens on the owner's real corpus with the
`research/retrieval_eval.py` harness over human-reviewed codings.

## U45 — hybrid RRF evaluation (default unchanged)

Harness: `src/corpus_kb/research/retrieval_eval.py` — recall@k, MRR,
nDCG@10, p95 latency for vector-only / FTS-only / RRF / RRF+reranker, with
relevance labels derived ONLY from human-reviewed assignments
(`research_assignments.status IN ('confirmed','overridden')`; query = code
name + brief definition).

**Status: `insufficient_data`.** No frozen labeled set exists yet, so no
metric has ever been produced and **no retrieval default was flipped**:
RRF stays the wired behavior it already was (SQL-side
`corpus.rrf_fusion`, Σ1/(k+rank), `search.rrf_k` = 60 now read from
config via `research/retrieval_settings.py`), and the default-flip gate
(`recommend_default`) demands a measured report where RRF ≥ vector-only on
recall@k inside the latency budget before anything changes.

New config keys (orchestrator wires into both YAMLs at the checkpoint; the
module defaults are live until then):

| key | default | reader |
|---|---|---|
| `search.rrf_k` | 60 | `RetrievalSettings.rrf_k` |
| `search.candidates_per_branch` | 50 | `RetrievalSettings.candidates_per_branch` |
| `search.return_top_k` | 10 | `RetrievalSettings.return_top_k` |
| `search.exact_filter_selectivity_threshold` | 0.05 | `RetrievalSettings.exact_filter_selectivity_threshold` |

## U21 — exact-scan fallback (wired)

When a filtered vector query's paired-count probe
(`matched/total`, same join shape as the arm) falls below
`search.exact_filter_selectivity_threshold` (0.05 — a documented starting
point, not a tuned optimum), the handler runs a filtered EXACT distance
scan (candidate CTE with no distance ORDER BY, so the planner cannot use
the ordered HNSW scan) instead of the ANN probe — identical result schema.
Unfiltered queries and failed probes fail OPEN to the ANN path. Wired in
`handlers/query_handler.py` (pgml + ollama arms; the config-off matryoshka
arm is benchmark-gated and excluded) and `research/retrieval.py`
(`_unit_arms` / `_exchange_arms`).

## U22/U46 — vector vs halfvec vs 256-d probe (ran; synthetic corpus)

Script: `scripts/bench_vector_types.py` (scratch schema, seeded corpus,
drops after run). Local run, 20000 rows × 1024 dims, k=10, 20 probes,
`hnsw.iterative_scan = strict_order`, seed 42. Both ANN arms were planner-
confirmed `Index Scan using` their HNSW indexes.

| arm | build | index size | recall@10 | p50 | p95 |
|---|---|---|---|---|---|
| `vector(1024)` HNSW | 25.7 s | 163.8 MB | 0.460 | 11.1 ms | 17.4 ms |
| `halfvec(1024)` cast HNSW | 9.9 s | 54.6 MB | 0.475 | 4.2 ms | 15.2 ms |
| 256-d probe HNSW | 3.5 s | 27.3 MB | 0.120 | 2.0 ms | 6.9 ms |

Honest caveats:

- **The corpus is random Gaussian** — worst-case for graph indexes and
  meaningless for MRL. Absolute recall (0.46) is a harness property, not a
  retrieval-quality statement; real embeddings cluster and score far
  higher. The 256-d probe's 0.12 likewise reflects that random vectors have
  no matryoshka structure; on real embeddings the analytics fixture
  measured a 256-vs-1024 recall delta of 0.0 (see
  `research/analytics_sql.py` module docstring).
- At 2000 rows the planner bypassed the halfvec cast index (seq+sort, so
  its "recall 1.0" was exact-scan trivial). Index-vs-index comparisons need
  corpora big enough that the planner prefers the index — use ≥10k rows.
- **U46 adoption gate** (`adoption_decision`, encoded in the script +
  tested): adopt halfvec on the main path only if recall loss ≤ 1.0 point
  (admin tolerance) AND gains are material (≥25% size or build reduction),
  implemented as a NEW index generation with the original `vector` column
  kept for rollback; never mixed. The measured deltas (recall +0.015, size
  −67%, build −61%, p50 −62%) satisfy the gate on this corpus, but the
  cutover decision requires rerunning this script on the REAL corpus — the
  synthetic run proves the harness, it does not decide the schema.

Binary quantization: **REJECTED** (spec v8 U46: needs heavy oversampling +
re-rank, reported low recall) — no code path shipped.

## U31 — embedder comparison (ran; authored fixture labels)

Script: `scripts/bench_embedders.py` (8-doc fixture corpus, 6 authored
queries, k=2, instruct prefix on the Qwen3 profiles). Local Ollama runs:

| profile | dims | recall@2 | MRR | p50 embed | p95 embed |
|---|---|---|---|---|---|
| nomic-embed-text (current default) | 768 | 0.750 | 0.833 | 1327 ms* | 11217 ms* |
| qwen3-embedding:4b | 2560 | 0.917 | 1.000 | 172 ms | 7294 ms* |
| qwen3-embedding:8b-q8_0 | 4096 | 1.000 | 1.000 | 190 ms | 20581 ms* |

\* p95 embeds are dominated by cold model LOAD (first call per model on a
laptop CPU/GPU boundary), not steady-state encode — treat p50 as the warm
signal. Six queries on eight docs is a smoke signal: directionally useful,
nowhere near a cutover basis.

Profile contract: every candidate is a versioned `EmbeddingProfile`
(model + dimensions + instruction + version). Changing embedder or
dimension = NEW versioned profile + NEW index generation + rebuild — never
in-place. `doctor_mismatch_note()` produces the doctor's mismatch text
when the installed config diverges from a profile.

## U30 — qwen3 reranker scoring (wired, config-off)

`rag/reranker.py:OllamaReranker` now implements the qwen3-reranker
contract: that model family has no rerank endpoint and emits a yes/no
judgment, so the score is `P(yes)/(P(yes)+P(no))` from token logprobs —
`/api/generate` with `logprobs=true` + `top_logprobs=<k>` (verified live on
Ollama 0.31.1; `logprobs` is bool-typed server-side, a number is rejected).
Config: `search.rerank.*` via `rag/rerank_settings.py` (`RERANK_CONFIG_DEFAULTS`),
**default OFF** per spec v6 U30 until the U45 harness shows an nDCG/recall
gain inside the p95 budget. Every Ollama client
(`coder_client`, `rag/embedder.py`, `rag/reranker.py`) is now timeout-bound
(120 s) and degrades instead of hanging.

**`qwen3-reranker` was NOT on the Ollama registry at implementation time**
(`pull model manifest: file does not exist`), so the production default
model `qwen3-reranker:4b` degrades to `not_evaluable` → un-reranked RRF
order with a logged warning. The scoring path itself was live-verified with
`qwen3:4b` as a stand-in (relevant docs 0.92–0.94, irrelevant 0.15–0.45 on
probe pairs) — but the general instruct model is NOT a reranker; pull a
real qwen3-reranker build before judging quality.

## U23 — pg_textsearch (NOT adopted)

- License: PostgreSQL License (per execution spec v6 §8; re-verify against
  the upstream repo before any run — `scripts/bench_pg_textsearch.py`
  carries the check as a documented prerequisite).
- Native Postgres FTS **stays the default**. `pg_textsearch` is
  benchmark-only (`scripts/bench_pg_textsearch.py`), gated behind the
  explicit `--enable-pg-textsearch` flag, and is NOT in the shipping image;
  without the flag it reports `not_enforced` and exits 0, with the
  extension absent it reports `insufficient_data`.
- Documented limitation recorded in every report: Boolean filtering
  combined with BM25 scoring is not yet a single index scan.
- ParadeDB `pg_search` is AGPL — **banned**, never benchmarked.

## Related settings modules (orchestrator checklist)

- `research/retrieval_settings.py` → `RETRIEVAL_CONFIG_DEFAULTS` (4 keys above)
- `rag/rerank_settings.py` → `RERANK_CONFIG_DEFAULTS` (`enabled` False,
  model `qwen3-reranker:4b`, base_url, `candidates` 100,
  `max_pair_tokens` 768, `score_floor` 0.15, `calibration` minmax,
  `timeout_seconds` 120)
- `research/exact_cache.py` → `CACHE_CONFIG_DEFAULTS` (`cache.enabled` True
  for deductive, `cache.inductive_enabled` False unless seeded)
