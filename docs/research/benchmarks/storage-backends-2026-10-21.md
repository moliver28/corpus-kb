# Storage backends: Postgres (HNSW) vs DuckDB (exact scan) — 2026-10-21

Committed 2026-10-21, Windows 10 (10.0.26200) x64, Python 3.11.5, Postgres 16.14
with pgvector 0.8.3 (HNSW over `halfvec(2560)`), DuckDB 1.4.5, numpy 2.4.6,
asyncpg 0.32.0. **Local-machine measurements on a synthetic corpus. Absolute
numbers are not deployment expectations; they move with the disk, the CPU, and
the Postgres configuration.** This is the benchmark gate of the storage
portability program (§10). Producer: `scripts/bench_storage.py`.

## Method

- Corpus: N chunks at 2560 dims, seeded synthetically through the storage
  layer. No embedder and no Ollama: chunk `i` rides one-hot anchor
  `i % 64` plus seeded noise, L2-normalized. Text is 60 sampled words;
  anchors 0–5 plant query words, so the lexical arm matches about 2/64 of
  the corpus at every size. Sizes: 1k / 10k / 50k chunks, one fresh store
  per size.
- Isolation: Postgres runs in a throwaway database per size (created and
  dropped by the run; reads set the RLS tenant GUC, one bench tenant).
  DuckDB runs in one throwaway file, deleted after the leg.
- Timing: 3 warmup runs, then 20 timed runs per operation;
  `time.perf_counter`; median and P95 (nearest-rank). Postgres gets
  `ANALYZE` after the seed (0.4–4.3 s, not in any number below). The
  DuckDB FTS index is rebuilt once before reads; its rebuild cost is
  reported separately.
- SQL shapes are the shipped arms, not new SQL: the Postgres kNN is the
  production relaxed-order ANN arm (`handlers/query_vector_arms.py`), the
  DuckDB kNN is the production exact cosine scan
  (`storage/duckdb_search.py`), the lexical arms are the production
  ts_rank / match_bm25 shapes, and the hybrid round-trip fuses through the
  pure-Python `rrf_fuse` that QueryHandler runs on BOTH engines.
- The EXPLAIN plan of the Postgres kNN was captured at every size. The
  HNSW index (`idx_chunks_vectors_hnsw_halfvec2560`) served the scan at
  1k, 10k and 50k — no planner fallback happened.

## Results

Median / P95 in seconds. Smaller is better.

### 1,000 chunks (Postgres 28.5 MB, DuckDB 21.8 MB on disk)

| Operation         | PG median | PG P95   | DuckDB median | DuckDB P95 |
| ----------------- | --------: | -------: | ------------: | ---------: |
| knn (k=10)        |    0.0018 |   0.0026 |        0.0158 |     0.0169 |
| lexical fts       |    0.0595 |   0.0626 |        0.0141 |     0.0167 |
| hybrid rrf        |    0.0618 |   0.0662 |        0.0306 |     0.0390 |
| list documents    |    0.0002 |   0.0002 |        0.0075 |     0.0090 |
| fetch chunk by id |    0.0001 |   0.0004 |        0.0025 |     0.0035 |

Seed write: Postgres 6.5 s (153.3 chunks/s), DuckDB 6.9 s (144.3 chunks/s).
DuckDB FTS rebuild: 0.09 s.

### 10,000 chunks (Postgres 171.7 MB, DuckDB 107.8 MB on disk)

| Operation         | PG median | PG P95   | DuckDB median | DuckDB P95 |
| ----------------- | --------: | -------: | ------------: | ---------: |
| knn (k=10)        |    0.0017 |   0.0019 |        0.0760 |     0.0847 |
| lexical fts       |    0.6016 |   0.6491 |        0.0193 |     0.0238 |
| hybrid rrf        |    0.6031 |   0.6429 |        0.1015 |     0.1114 |
| list documents    |    0.0001 |   0.0002 |        0.0073 |     0.0081 |
| fetch chunk by id |    0.0001 |   0.0002 |        0.0030 |     0.0039 |

Seed write: Postgres 103.9 s (96.3 chunks/s), DuckDB 72.2 s (138.5 chunks/s).
DuckDB FTS rebuild: 0.51 s.

### 50,000 chunks (Postgres 779.1 MB, DuckDB 873.2 MB on disk)

| Operation         | PG median | PG P95   | DuckDB median | DuckDB P95 |
| ----------------- | --------: | -------: | ------------: | ---------: |
| knn (k=10)        |    0.0022 |   0.0031 |        0.4359 |     0.4584 |
| lexical fts       |    0.7434 |   0.8118 |        0.0379 |     0.0411 |
| hybrid rrf        |    0.7822 |   0.8076 |        0.4868 |     0.5420 |
| list documents    |    0.0003 |   0.0004 |        0.0104 |     0.0133 |
| fetch chunk by id |    0.0001 |   0.0002 |        0.0051 |     0.0070 |

Seed write: Postgres 680.8 s (73.4 chunks/s), DuckDB 348.3 s (143.5 chunks/s).
DuckDB FTS rebuild: 2.12 s.

## Interpretation

- **The ~100k exact-scan ceiling claim holds, now with numbers.** The
  DuckDB vector scan costs 16 ms at 1k, 76 ms at 10k, and 436 ms at 50k.
  The scan grows near-linearly with corpus size; extrapolated to 100k it
  lands near 0.9 s per vector query. That matches the program doc's
  working ceiling of ≤ ~100k chunks at 2560 dims for single-user
  interactive use. Postgres HNSW stays flat (~2 ms) at every size.
- **Hybrid search at 50k is a DuckDB win, despite the scan.** Postgres
  lexical search is the slow arm (0.74 s at 50k): the GIN index answers
  the match fast, but `ts_rank` recomputes `to_tsvector` for every
  matching row. The DuckDB bm25 macro stays at 38 ms. So the whole
  DuckDB hybrid round-trip (0.49 s) beats the Postgres one (0.78 s) even
  though Postgres wins the vector arm by about 200x. Dense-heavy
  workloads need Postgres; lexical-heavy workloads do not.
- **Bulk insert diverges with size.** Postgres degrades (153 → 96 → 73
  chunks/s) because the seed write carries HNSW index maintenance plus
  26 KB of vector text per row. DuckDB stays flat (~140 chunks/s). At
  50k the DuckDB seed writes twice as fast. `migrate-data` moves should
  expect the same shape.
- **Point reads favor Postgres.** Single-chunk fetch and page listing
  are 20–90x faster on Postgres. The DuckDB gateway pays a thread hop
  and a parse per statement.
- **Disk cost is comparable** (779 MB Postgres vs 873 MB DuckDB at 50k).
- **The DuckDB FTS rebuild is a real recurring cost** (0.09 → 0.51 →
  2.12 s, roughly linear). It runs after every write wave that moves the
  count watermark. It stays small next to the exact scan, but it is not
  free.

## What is NOT measured

Concurrency and multi-client behavior (DuckDB is single-writer by
design). Cold-cache reads. HNSW build time in isolation (it sits inside
the Postgres seed write). The `vss` extension on DuckDB. pgml embeddings.
Real embedder latency (vectors are synthetic). Real-text statistics (the
corpus has uniform word statistics). `memory_limit` tuning. WAL and
checkpoint pressure. Antivirus effects on the DuckDB file.

## Verdict

The benchmark validates the storage portability program's guidance: DuckDB
is the right engine below roughly 100k chunks at 2560 dims for the local
single-user case, and the exact-scan vector arm is the binding constraint
that defines that ceiling. Teams above the ceiling should move to Postgres
with `migrate-data`; the numbers above say the move buys a 200x faster
vector arm and much faster point reads, at the cost of slower bulk writes
at this seed shape and slower ts_rank re-scoring on lexical-heavy hybrid
queries.
