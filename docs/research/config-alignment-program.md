# Config Alignment Program

Status: CLOSED (all four waves shipped). Started 2026-10-14 at master
`6fe7221`.
Goal: align the default config to the user-supplied target, close the real
feature gaps, and keep every CI gate green per wave.

Shipped:

- **W1** — PR #101: halfvec(2560) schema + config namespace v2 + the
  alias layer (`src/corpus_kb/config_aliases.py`).
- **W2** — PR #102: shadow boundary adapters (slide/turn grouping by
  source type).
- **W3** — PR #103: contextualize v2 + reranker generative-binary +
  the `return_top_k` contract (catalogue k default now 20).
- **W4** — this branch (`feature/config-align-w4-topics`): the `topics`
  block built end to end — the `[topics]` extra (bertopic), the offline
  engine over stored embeddings, LLM label reduction, migration 026,
  and the `topics_build`/`topics_list` op ladder (catalogue + parity +
  OPERATIONS). uv.lock regenerated (bertopic + plotly added).

## Surface parity wave (2026-10-13, post-closeout)

Full surface parity shipped on `feature/feat-full-surface-parity` (based
on master `81af1f6`): every catalogue operation is reachable on all four
surfaces (MCP, CLI, HTTP, socket) and the parity gate enforces exactly
that.

- The 11 registry-only ops joined the contracts catalogue with real
  envelope key-sets: `research_ingest`, `coding_run`, `inductive_run`,
  `codebook_promote`, `research_report`, `review_execute`,
  `notebook_evidence`, `notebook_uncoded`, `notebook_overlap`,
  `research_cycle`, `research_demo`. `corpus-kb operations` prints 0
  skipped; the catalogue holds 34 ops.
- New tool layers: `tools/query_tools.py` (`query`, `verify_answer`),
  `tools/graph_tools.py` (`add_entity`, `add_relation`), and
  `tools/demo_tools.py` (`research_demo`). The socket dispatch moved to
  a table (`api/socket_ops.py`): 34 arms, one per op, generated for the
  research/release families from the same table the HTTP routes use.
- New CLI front doors: `cli_graph.py` (add-entity, add-relation,
  list-documents, delete-document), `cli_query.py` (query,
  verify-answer), `cli_ingest_text.py` (ingest-text). Honest exits
  0/1/3; `--json` prints the exact handler envelope.
- `research_cycle` and `research_demo` gained HTTP + MCP + socket legs
  as synchronous long-running calls. `codebook_promote` stays an
  explicit human act on every surface. `TRANSPORT_ALLOWLIST` is empty.
- The deep parity gate now requires all four surfaces and all four legs
  per catalogue row. MCP tool count: 29 → 34.

## Decisions (locked by the user, 2026-10-14)

1. Default embedding = `qwen3-embedding:4b` at its **native 2560 dims**, with
   `storage.vector_type: halfvec`. No reliance on MRL truncation.
2. Schema becomes `halfvec(2560)` with a real HNSW index
   (`halfvec_cosine_ops`). halfvec is REQUIRED at 2560: pgvector HNSW caps
   `vector` at 2000 dims and `halfvec` at 4000.
3. The target's section names are canonical: `storage.*`, `ingest.chunking.*`,
   `ingest.contextualize.*`, `retrieval.*`, `reranking.*`, `topics.*`.
   Legacy namespaces (`search.*`, `ingest.hierarchy.*`, `contextual.*`) stay
   readable as aliases. Canonical wins. Alias use logs a deprecation warning.
4. `embedding.provider` default flips `pgml` → `ollama`. PostgresML cannot
   practically serve a 4B embedding model in-database.
5. `mrl_trained: true` (the qwen3-embedding family is MRL-trained). This
   unlocks the matryoshka arm and the Ollama `dimensions` passthrough.
6. `query_instruction: true` gates the existing `QUERY_INSTRUCTION_PREFIX` at
   `instruct()`. The prefix text itself does not change (it is cache-keyed).
7. Arm candidate counts get wired into the LIVE limits (today the knob is
   benchmark-only): `retrieval.vector_candidates` / `lexical_candidates`,
   effective limit = `max(candidates, k * 2)`.
8. `expand_to_parent` moves to `retrieval.*` and flips to `true`. The
   expansion code already exists and is fail-open.
9. HNSW `m` / `ef_construction` become config keys. Migration 025 DDL uses
   m=16, ef_construction=128. The doctor cross-checks config vs the actual
   index options.
10. `reranking.return_top_k` is accepted+validated in W1 and becomes the
    default `k` (catalogue contract change 10 → 20) in W3.
11. The model switch invalidates ALL existing embeddings. Migration 025
    re-types the main + coding vector columns to `halfvec(2560)`; old rows
    become stale (NULL embedding). `ingest-reembed` learns to rebuild
    stale rows, not only degraded-flagged rows. Text/documents/chunks are
    untouched.

## Namespace mapping (canonical ← legacy alias)

| Canonical (target) | Legacy (current) | Notes |
| --- | --- | --- |
| `embedding.{model,dimensions}` | same | value flip 4b/2560; `provider` → ollama |
| `embedding.query_instruction` | (none; ungated constant) | new bool key |
| `embedding.mrl_trained` | same | false → true |
| `storage.vector_type` | (none) | new key; `halfvec` only supported value for now |
| `ingest.chunking.{child,chunk,parent,overlap}_tokens, respect_boundaries` | `ingest.hierarchy.*` (same values) | rename; numbers already match target |
| `ingest.chunking.enabled` | `ingest.hierarchy.enabled` | rename |
| `retrieval.expand_to_parent` | `ingest.hierarchy.expand_to_parent` | move + flip false→true |
| `retrieval.{mode, vector_candidates, lexical_candidates}` | `search.candidates_per_branch` (inert) | new keys; alias feeds both |
| `retrieval.rrf_k` | `search.rrf_k` | move; value already 60 |
| `retrieval.hnsw.*` | `search.hnsw.*` | move; query-time trio already at target values; add m/ef_construction |
| `retrieval.{exclude_degraded, qa_context_in_fts, exact_filter_selectivity_threshold, candidate_multiplier, self_query, matryoshka_*}` | `search.*` (same names) | move unchanged |
| `reranking.*` | `search.rerank.*` | move; model/candidates/max_pair_tokens already at target; add `return_top_k` |
| `ingest.contextualize.*` | `contextual.*` | move; add backend/prompt_profile/max_context_tokens/document_first_prompt/apply_to; model qwen3:4b → qwen3:8b (W3) |
| `topics.*` | (none) | new block, built in W4 |

Alias precedence: canonical key present → legacy ignored. Canonical absent →
legacy copied in + deprecation warning. Both YAMLs stay byte-identical and
carry only canonical keys.

## Audit result: what already exists (do not rebuild)

- Child/chunk/parent hierarchy: `chunking/hierarchy_tree.py` +
  `chunking/hierarchy_settings.py` (defaults exactly 128/512/2048/64 and the
  four target boundary kinds), shadow storage (migration 021), query-time
  parent expansion (`rag/parent_expand.py`).
- Contextual retrieval: `rag/contextualizer.py` (document-first by
  construction, Stage 1 slot, never raises into ingest), blurb applied to
  embedding input AND lexical FTS.
- Reranking: `rag/reranker.py` OllamaReranker (qwen3 judgment scoring via
  generate logprobs), honest fail-open fallback, Pgml/Fake/Identity variants.
- HNSW query-time settings + pgvector version gate (fail-closed, ≥ 0.8).
- RRF fusion with real `rrf_k` parameter and deterministic tiebreak (024).

## Real gaps

| Gap | Wave |
| --- | --- |
| Main + coding columns `vector(4096)` → `halfvec(2560)`; HNSW index real; schema.sql end-state; width guard; doctor matrix; reembed stale rows | W1 |
| Config namespace v2 (mapping above), alias layer, validate_configs keys, three-way default sync | W1 |
| Embedding default flip (4b/2560, provider ollama, mrl true) + `query_instruction` gate + `dimensions` passthrough + returned-dim validation | W1 |
| `retrieval.mode` enum + live candidate wiring + `expand_to_parent` flip + hnsw m/ef_construction keys | W1 |
| Shadow path respects only heading groups; slide adapter missing; transcript qa_pair/speaker_turn adapters exist but are uncalled | W2 |
| Contextualize: 5 keys missing, model default qwen3:4b → qwen3:8b, prompt profiles, apply_to write-time semantics | W3 |
| Reranker vs live Ollama 0.31.1: top-level `logprobs` returned HTTP 400 in a probe; verify the exact call shape, fix or add honest generative fallback | W3 |
| `reranking.return_top_k` contract change (default k 10 → 20) + docs regen | W3 |
| Topics: nothing exists (only a c-TF-IDF namecheck). BERTopic as `[topics]` extra, offline pipeline, tables, op surface | W4 |
| Installer profiles still name nomic/8b — incoherent with a 2560-locked schema | W1 |

## Waves

- **W1 dimension regime + config v2** — one agent, sequential. Exit: full
  pytest green (incl. live-PG), migration 025 applies + rolls back on a real
  database, `poe check` green, PR CI green.
- **W2 boundary adapters** — shadow ingest picks the segment adapter by
  source type (pptx → slide grouping; transcript → turns/qa_pair; else
  heading). Small wave.
- **W3 contextualize v2 + reranker** — keys + profiles + apply_to; reranker
  live verification against Ollama 0.31.1; `return_top_k` contract change.
- **W4 topics + closeout** — BERTopic optional extra (`topics.enabled: false`
  default), topic tables + offline build op (catalogue + parity + OPERATIONS),
  uv.lock regen (noted), docs closeout, program status flip.

## CI map (every wave)

ruff check + format, pyright (basic), validate_configs, xenon max-absolute C,
vulture 0-hit (extend `tests/whitelist.py` for new names), deptry,
import-linter contracts, parity gate (envelope key-sets — new ingest keys must
stay conditional), `poe gen --check` when the catalogue or registry changes,
Vale (docs outside `docs/research/**`; sentences ≤ 20 words at error level),
textstat plain-language gates, commit-validate (conventional, capital first,
per-line), branch naming `feature/*`, license self-heal (auto-commit on
drift).

## Runtime facts (probed 2026-10-14, Ollama 0.31.1)

- `/api/embed` honors a `dimensions` param (MRL truncation works); 4b native
  = 2560.
- No `/api/rerank` and no `/v1/rerank` (404). Top-level `logprobs` on
  `/api/generate` → HTTP 400. The reranker's exact call shape needs live
  verification in W3.
- Pulled locally: qwen3-embedding:4b, qwen3:4b, nomic, qwen3-embedding:8b.
  Not pulled: qwen3:8b, qwen3-reranker:4b. Doctor should surface availability.

## Risks

- 025 is a one-way retype for vector data (by design). Text survives; vectors
  rebuild via reembed. Rollback file restores the 4096 column shape but not
  old vectors.
- Research grain (016/021, `research.embedder.dimensions: 1024`,
  halfvec(1024) cast probes) is a separate contract — DO NOT touch in W1.
- `test_schema_reference.py`, `test_chunk_hierarchy.py`, `test_rrf_sql.py`
  and ~8 more files pin current numbers/types; move them in the same change.
- Late chunking will fall back to pooled (honest note) with 4b unless someone
  supplies HF 4B weights — the coherence check already handles this.
