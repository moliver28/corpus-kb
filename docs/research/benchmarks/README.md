# Benchmark artifacts

Raw benchmark measurements for the U1–U48 retrieval/performance upgrades live
in [`docs/retrieval-benchmarks.md`](../../retrieval-benchmarks.md) (committed
2026-10-10, Windows x64 / Postgres 17 + pgvector 0.8.3 / Ollama 0.31.1 —
local-machine measurements, not CI truth; the corpora are synthetic fixtures
so absolute numbers are not deployment expectations).

Producing scripts:

- `scripts/bench_vector_types.py` — full `vector` vs `halfvec`, 256-d probe,
  scan modes (U22/U46); binary quantization documented-rejected.
- `scripts/bench_embedders.py` — nomic vs qwen3-embedding 4B/8B on fixture
  corpora (U31); embedder/dimension changes require a new versioned embedding
  profile, never an in-place change.
- `scripts/bench_pg_textsearch.py` — pg_textsearch BM25 comparison
  (`--enable-pg-textsearch` gate; PostgreSQL License verified; native FTS
  stays the default) (U23).
- `research/retrieval_eval.py` — recall@k / MRR / nDCG@10 / p95 harness over
  human-reviewed labels (U45); reports `insufficient_data` until a frozen
  labeled set exists.

Cutover decisions that remain deliberately open pending a real-corpus rerun:
halfvec adoption on the main path, `expand_to_parent`, and any embedding
default change (the committed qwen3-embedding:4b @ 1024 default is
unconditional; benchmarks inform cutover only).
