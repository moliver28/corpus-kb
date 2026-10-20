# W0 Spike Findings — Search / FTS (agent S2)

Target: duckdb 1.4.5, shared venv (`F:/Documents/ZCode/Corpus-kb/.venv`), fts+vss extensions from warmed offline cache. All probes runnable from `findings/probes/`.

## PLAN-CHANGING SURPRISES (read first)

1. **match_bm25 API changed in DuckDB 1.4.** It is now a **scalar macro called in the SELECT list**, not a table function in FROM. The classic `FROM fts_main_chunks.match_bm25(chunk_id, 'x') WHERE score IS NOT NULL` shape — used in all pre-1.4 docs and likely in our plans — raises `CatalogException: Table Function with name match_bm25 does not exist!` on 1.4.5.
2. **`array_norm` does not exist on 1.4.5.** The degraded-mode "norm > 0" guard needs another formula (`sqrt(array_inner_product(v, v)) > 0`).
3. **`SET default_null_order = 'postgres'`** is an accepted alias that equals exactly the Postgres parity target (`NULLS_LAST_ON_ASC_FIRST_ON_DESC`) — but it is a **per-connection setting; it reverts to `NULLS_LAST` on reopen**.
4. **duckdb-fts issue #15 (conjunctive + stopword → 0 rows) is PRESENT** in 1.4.5's bundled fts.
5. **The FTS index never auto-updates**: rows inserted after `create_fts_index` are invisible to `match_bm25` (confirmed empirically).

---

## Spike 1 — match_bm25 prepared-statement binding

**VERDICT:** Works. Both `$1` and `?` bind the query string — but only in the new scalar-macro shape.

**EVIDENCE** (`probe1_bm25_binding.py`, file-backed DB):

Fails on 1.4.5:
```
CatalogException: Catalog Error: Table Function with name match_bm25 does not exist!
LINE 1: SELECT chunk_id FROM fts_main_chunks.match_bm25(chunk_id, $1) WHERE score IS NOT NULL
```
(same for `?`; same for all-6-positional-args; `con.prepare` does not exist in the Python API)

Exact working shape (matches the official 1.4 docs "retrieval macro" pattern):
```sql
SELECT chunk_id, score
FROM (SELECT *, fts_main_chunks.match_bm25(chunk_id, $1) AS score
      FROM chunks) sq
WHERE score IS NOT NULL
ORDER BY score DESC
-- con.execute(sql, ["needle"])  -> [('c', 0.30225458970204244), ('a', 0.21678949882077533)]
```
- `$1` form, `?` form, `$1` used twice in one statement, and named args (`conjunctive := 0`) mixed with a positional param all work and give identical scores to the literal form.
- Catalog truth: `duckdb_functions()` shows `match_bm25` with `function_type='macro'`, params `['docname','query_string','fields','k','b','conjunctive']` (new `fields` param in 1.4). Signature per docs: `match_bm25(input_id, query_string, fields := NULL, k := 1.2, b := 0.75, conjunctive := 0)`.
- The macro + index tables (`dict, docs, fields, stats, stopwords, terms`) persist in the DB file; queries work after reopen **without** an explicit `LOAD fts`.

**RECOMMENDATION:** `sp-duckdb-search` must emit the subquery scalar shape above. Parameter binding removes all escaping/quoting concerns for user input.

**AFFECTED-WP:** sp-duckdb-search, sp-dialect

## Spike 2 — query-string robustness + conjunctive/stopwords

**VERDICT:** Nothing raises. Every malformed/edge query silently returns an empty result set. Wildcards are silently unsupported. Issue #15 IS present.

**EVIDENCE** (`probe2_robustness.py`, 7-doc corpus, param-bound):

| Query | Result |
|---|---|
| `unclosed "quote` | empty, no error (also verified param ≡ literal) |
| `hyphen-word` | MATCHES doc containing it (score 1.54) |
| `it's` | empty — `it`/`s` are stopwords after tokenization |
| `知识库` | **empty even though a doc contains it** — the whitespace tokenizer keeps the whole CJK run (`知识库是知识管理的核心`) as one token; no CJK segmentation |
| `AND OR NOT` | empty (treated as stopwords, not boolean ops) |
| `''`, `' '`, punctuation-only, 500-char random text | empty, no error |
| `*`, `needle*`, `知识*` | **empty — no wildcard/prefix support; silent no-match** |

Conjunctive (`conjunctive := 1`) — issue #15 reproduced:
- `'the dog'` → **0 rows** (disjunctive finds the doc, score 0.635)
- `'the quick'` → **0 rows** (doc d1 contains `quick`!)
- `'quick dog'`, `'fox dog'` → d1 found (control: works without stopwords)
- Cause visible in the macro definition: `HAVING count(DISTINCT termid) = (SELECT count_star() FROM tokens)` where `tokens` is the **unfiltered** token list — `tokenize('the dog')` returns `['the','dog']`; stopwords are removed at index time only, so the count can never match when a stopword is present.

**RECOMMENDATION:** (a) For `conjunctive := 1`, strip stopwords from the query string client-side before binding (stopword list = `fts_main_chunks.stopwords`, column `sw`, 571 rows). (b) Treat empty results as legitimate; there is no error channel to handle for bad strings. (c) Do not promise prefix search; if needed later, expand `term*` client-side against `fts_main_chunks.dict`. (d) CJK content needs external segmentation upstream or must fall back to vector search — document this.

**AFFECTED-WP:** sp-duckdb-search, sp-dialect

## Spike 3 — array_cosine_distance / similarity / norm

**VERDICT:** Research prediction confirmed exactly. `array_norm` is MISSING on 1.4.5.

**EVIDENCE** (`probe3_cosine.py`):
- Zero vector vs normal vector: `array_cosine_similarity` → **exactly `-1.0`**, `array_cosine_distance` → **exactly `2.0`** (clamp prediction confirmed). Zero vs zero is also `-1.0` / `2.0`.
- Identical vectors: `1.0` / `0.0`. Opposite (`x` vs `-x`): `-1.0` / `2.0`. Orthogonal: `0.0` / `1.0`.
- NULL **elements** inside an array → raises `InvalidInputException: array_cosine_distance: left argument can not contain NULL values` (not silent). Whole-arg NULL → returns NULL.
- Return type is `FLOAT` for `FLOAT[n]` inputs (element type follows input).
- Existence: `array_cosine_distance` ✅, `array_cosine_similarity` ✅, `array_distance` ✅, `array_inner_product` ✅, **`array_norm` ❌** (`CatalogException: Scalar Function with name array_norm does not exist!`).
- These functions are core/autoloaded: they work on a fresh connection without `LOAD vss`.

**RECOMMENDATION:** Degraded-mode guard: `sqrt(array_inner_product(v, v)) > 0` (computes L2 norm without array_norm). A zero vector is also detectable via `array_cosine_similarity(v, v) = -1.0` (normal vectors give 1.0; a zero vector gives −1.0 against itself). Downstream scoring must tolerate distance 2.0 / similarity −1.0 for zero-vector (degraded) embeddings.

**AFFECTED-WP:** sp-duckdb-search, sp-dialect

## Spike 4 — default_null_order

**VERDICT:** Exactly two canonical dual-mode values plus three dialect aliases; `'postgres'` gives exact PG parity; setting is session-local, not persisted.

**EVIDENCE** (`probe4_null_order.py`):
- Accepted (case-insensitive): `NULLS_FIRST`, `NULLS_LAST`, `NULLS_FIRST_ON_ASC_LAST_ON_DESC`, `NULLS_LAST_ON_ASC_FIRST_ON_DESC`, `sqlite` (= NULLS_FIRST_ON_ASC_LAST_ON_DESC), `mysql` (= same), **`postgres` (= NULLS_LAST_ON_ASC_FIRST_ON_DESC)**. Everything else (incl. `postgresql`, `sql_default`, `auto`) → `Parser Error: Unrecognized parameter ... expected either NULLS FIRST, NULLS LAST, SQLite, MySQL or Postgres`.
- Fresh connection default: `NULLS_LAST`.
- DESC with `NULLS_LAST_ON_ASC_FIRST_ON_DESC`: `NULL, NULL, nan, 3.0, 2.5, 1.0` → **DESC = NULLS FIRST = Postgres parity**; ASC gives NULLS LAST = PG default. Confirmed for the mission's target string `'NULLS_LAST_ON_ASC_FIRST_ON_DESC'`.
- NaN sorts as a number greater than all finite values in every mode — same as Postgres.
- Explicit `ORDER BY x DESC NULLS FIRST` overrides the setting.

**RECOMMENDATION:** `sp-duckdb-migrations` / every search connection should issue `SET default_null_order='postgres'` at connection setup (reads better than the long form, identical semantics). Must be re-issued on **every** connection — verified it reverts to `NULLS_LAST` after reopen of a file DB.

**AFFECTED-WP:** sp-dialect, sp-duckdb-migrations, sp-duckdb-search

## Spike 5 — FLOAT[2560] typing

**VERDICT:** Fully workable. Python lists and numpy arrays insert and bind; the query param needs an explicit `::FLOAT[2560]` cast; varchar→FLOAT[2560] direct CAST works for no-space text only.

**EVIDENCE** (`probe5_float_array.py`):
- `CREATE TABLE t(embedding FLOAT[2560])` OK; `DESCRIBE` shows `FLOAT[2560]`.
- Insert a 2560-float Python list via `executemany`: OK. Insert a numpy `float32` array directly (no `.tolist()`): OK. numpy array directly as a query param: OK.
- `ORDER BY array_cosine_distance(embedding, $1::FLOAT[2560]) LIMIT 3` with a list param: OK.
- Same query with `?` and **no** cast: `BinderException: No function matches ... array_cosine_distance(FLOAT[2560], DOUBLE[])` — Python float lists bind as `DOUBLE[]`, so **the `::FLOAT[2560]` cast on the param is mandatory**.
- `CAST('[0.1,0.2,...]' AS FLOAT[2560])` from varchar: **works when there are no spaces after commas**; `ConversionException` with `', '` separators. pgvector `halfvec` text `[0.1,0.2]` parses fine (f32 rounding visible: 0.1 → 0.10000000149011612). Set-based migration via `INSERT ... SELECT CAST(? AS FLOAT[2560])` / CTAS is cheap (2560-dim CTAS CAST ≈ 1.5 ms). Fallback `list_transform(string_split(...), x -> x::FLOAT)` yields untyped `LIST[FLOAT]` (not fixed-size ARRAY) — not equivalent.
- Dimension mismatch: `BinderException: array_cosine_distance: Array arguments must be of the same size`.

**RECOMMENDATION:** In the migration (halfvec-text re-parse): `replace(vec_text, ' ', '')` then `CAST(... AS FLOAT[n])`; stage source text in a temp table and CAST set-based. Always write `$1::FLOAT[n]` in query SQL.

**AFFECTED-WP:** sp-duckdb-migrations, sp-duckdb-search

## Spike 6 — FTS lifecycle microbench

**VERDICT:** Cheap at this scale; rebuild-per-delta watermark design is viable. Index schema name `fts_main_chunks` confirmed. Index does NOT auto-update.

**EVIDENCE** (`probe6_fts_bench.py`, file DB, 5000 rows × 80 words, VARCHAR uuid keys):
- Build table + insert 5000 rows: 3.9 s
- `PRAGMA create_fts_index('main.chunks','chunk_id','text')`: **0.51 s**
- `PRAGMA drop_fts_index('main.chunks')`: 0.004 s; recreate: **0.55 s** (drop+recreate ≈ 0.55 s total)
- `match_bm25` query (param-bound, LIMIT 10): **16–20 ms** (1-term and 3-term)
- `match_bm25` resolves in schema **`fts_main_chunks`** (confirmed via catalog).
- Insert of a doc with a unique new term after indexing → search returns **0 rows** for that term: the docs' "index will not update automatically" warning is real.

**RECOMMENDATION:** Budget ≈ 0.5 s per 5000×80-word docs for a full rebuild; treat as roughly linear and schedule rebuilds after ingest commits (watermark design). Since stale rows also remain in the index on insert-only workloads, any incremental strategy must recreate (drop+create) after batch inserts — drop is effectively free.

**AFFECTED-WP:** sp-duckdb-search

## Spike 7 — vss HNSW persistence + WAL replay + compact

**VERDICT:** Persistence flag required (exact message recorded). Hard-exit recovery is silent and successful. The LOAD-ordering rule bites only for vss **settings**, not for the `<->` operator. `hnsw_compact_index` works.

**EVIDENCE** (`probe7_vss_drill.py` + `probe7a_child.py`, file DB):
- Without the flag: `BinderException: Binder Error: HNSW indexes can only be created in in-memory databases, or when the configuration option 'hnsw_enable_experimental_persistence' is set to true.`
- With `SET hnsw_enable_experimental_persistence = true`: `CREATE INDEX idx_hnsw ON docs USING HNSW (embedding) WITH (metric='cosine')` succeeds on a file DB.
- WAL-replay drill: child process inserts 200 rows, creates the index, then `os._exit(1)` without close. After exit the `.wal` file was absent (already checkpointed) and the `.duckdb` file held all data. Reopen **with** `LOAD vss` first: count, plain `ORDER BY`, and `embedding <-> $1::FLOAT[8]` all return correct results; index present in `duckdb_indexes()` (stored DDL shows `USING HNSW (embedding)` — the WITH clause is not echoed).
- Reopen **without** vss loaded: table queries and `<->` still work (duckdb autoloads vss when the operator is referenced), but `SET hnsw_enable_experimental_persistence = true` without a prior `LOAD vss` fails: `CatalogException: Setting with name "hnsw_enable_experimental_persistence" is not in the catalog, but it exists in the vss extension. Please try installing and loading the vss extension: INSTALL vss; LOAD vss;`
- After `DELETE FROM docs WHERE id % 2 = 0` (200→100 rows): `PRAGMA hnsw_compact_index('idx_hnsw')` → OK; subsequent `<->` queries correct.

**RECOMMENDATION:** Codify: any vss **setting** (including the persistence flag) requires `LOAD vss` first on that connection — this is the concrete failure behind the LOAD-before-ATTACH ordering rule. Plain vector queries survive reopen of a persisted HNSW index even after a hard crash; no manual repair was needed. Run `hnsw_compact_index` after large deletes.

**AFFECTED-WP:** sp-duckdb-search, sp-duckdb-migrations

---

## Probe index

| File | Spike |
|---|---|
| `probes/probe1_bm25_binding.py` | 1 (final, scalar shape, file-backed) |
| `probes/probe1b_debug_macro.py`, `probes/probe1c_call_shapes.py` | 1 (diagnosis of the API change) |
| `probes/probe2_robustness.py` | 2 |
| `probes/probe3_cosine.py` | 3 |
| `probes/probe4_null_order.py` | 4 |
| `probes/probe5_float_array.py` | 5 |
| `probes/probe6_fts_bench.py` | 6 |
| `probes/probe7_vss_drill.py`, `probes/probe7a_child.py` | 7 |
