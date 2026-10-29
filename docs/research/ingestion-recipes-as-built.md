# Ingestion recipes and the calibrate-then-lock sweep — as built

Status: IMPLEMENTED. PRs #211–#214 (October 2026) shipped the query-time
recipe family, the calibrate-then-lock sweep, the doctor reranker probe,
the `transcript_meta` validation mode, and the ingest-run project link.
This document records what actually landed, the decisions behind it, and
the evidence base. It supersedes the plan in
[ingestion-validation-chunking-plan.md](ingestion-validation-chunking-plan.md)
where the two disagree. The user-facing walk-through is
[docs/VALIDATION.md](../VALIDATION.md).

## 1. The query-time-only decision

The plan sketched ingest-time knobs: candidate chunkers, segmenters, and
per-type recipes applied when the corpus is written. The as-built code
rejects that whole category. The reasons:

- **Ingest-time knobs were phantom config.** The retrieval recipe keys
  (`retrieval.rrf_k`, `retrieval.vector_candidates`,
  `retrieval.lexical_candidates`, `reranking.*`) are read at rank time.
  No ingest path ever consumed them, so presenting them as chunking
  choices would have been config that silently did nothing.
- **One corpus, many lenses.** Every candidate ranks the SAME ingested
  corpus. A sweep therefore measures exactly what the query-time seam
  can change, and a win costs one config edit instead of a re-ingest of
  the corpus. `eval/recipes.py` states the contract: "recipes change
  rank time, never the ingest."

`ALLOWED_OVERRIDES` holds the six keys above — nothing else. Unknown or
forbidden keys raise a `ValidationError` at profile load, before any
engine exists, so a typo never burns a sweep. `MAX_CANDIDATES = 8` caps
the family.

By design in v1, candidates differ by query-time recipes only. Candidate
names are validated against the ingestion-strategy registry, and the
winner's name lands in the lock as `proposed_default_strategy`; the
names act as family-member labels. Document-side sweeps (chunker arms
ranked through the same family machinery) are issue #195. Span-anchored
labels (already shipped for `eval`) are the groundwork for them.

## 2. The one ranked-children seam

Search, `eval run`, and the sweep all rank through one implementation:
`research.retrieval.ranked_children`. The sweep engine builds a
`ResearchQuery` per golden query, applies the candidate's overrides to a
deep copy of the config, and calls the same seam the live server uses.
Consequences:

- Sweep numbers transfer to live search; there is no second rank
  implementation to drift (zero-drift rule 1: one operation, one
  handler, many surfaces).
- `reranking.enabled` toggles the reranker argument of the same seam.
  `reranking.over_retrieve_n` and `reranking.score_floor` are accepted
  for config consistency; the rank seam itself does not read them.

## 3. The sweep: one scratch tenant, cheap passes

`ScratchTenantEngine` (tools/pipeline/calibrate_sweep_engine.py) gives
one synthetic tenant per (profile, golden) pair — a dedicated `uuid5`
namespace makes a collision with a real tenant impossible. The sample is
ingested once; every further candidate pass is a rank pass only. The
plan's throwaway databases became throwaway tenants: the same isolation
with zero DDL. Cleanup is a set of parameterized DELETEs that ride the
documents cascade.

The engine requires Postgres: the research arms run pgvector + SQL
fusion, which DuckDB does not serve. A `duckdb` backend raises
`DatabaseUnavailableError` before any connection opens. A zero-vector
probe at open answers the degraded flag, so a downed embedder refuses to
score instead of producing fake numbers.

## 4. Statistics: paired tests, Holm-corrected across the family

`eval/stats.py` answers one question for the whole family: is any
challenger really better than the baseline? Two methods, named honestly
in every result:

- **Fisher randomization (ranx)** when the optional `calibrate` extra is
  installed — the paired test the eval protocol pre-registers
  ([arXiv:1905.11096](https://arxiv.org/abs/1905.11096) on permutation
  tests for IR).
- **Sign test** otherwise — distribution-free, no p-value claim, stated
  as such.

Queries pair by query text, never by position; a differing query set is
a hard error. `compare_all` runs every baseline-versus-challenger
pairing and applies the Holm step-down correction, so a five-candidate
family cannot win on a p-value that would not survive family-wise
correction. The reporting-rigor baseline is
[arXiv:2501.03930](https://arxiv.org/abs/2501.03930) (significance
testing with Wilcoxon + Benjamini–Hochberg in retrieval reports);
[arXiv:2210.11612](https://arxiv.org/abs/2210.11612) informs the
`min_queries` floor — test power at small query counts is the reason a
thin sweep refuses to answer rather than guessing.

## 5. The outcome taxonomy

`CalibrationResult.status` is one of three values:

| Outcome | Condition | Writes |
|---------|-----------|--------|
| `success` | One challenger cleared the Holm-corrected bar with a positive delta. | The lock. |
| `no_significant_winner` | The family scored; nothing cleared the bar. | Nothing. |
| `insufficient_data` | Degraded embeddings, labels that match nothing, fewer than `min_queries` scored queries, or missing p-values (sign-test path without ranx). | Nothing; a reason names the fix. |

Only `success` writes the lock. Winner selection: the significant
challenger with the highest nDCG@10; ties break on recall@k, then on
candidate name ascending (a deterministic total order, per zero-drift
rule 9). Metrics come from the shared `eval` scoring code, so sweep
numbers and `eval run` numbers are the same numbers.

## 6. The lock, and the manual apply path

`<profile>.lock.json` is written atomically (tmp file + replace) and is
deterministic for identical inputs, except `date`. The PR-2 keys —
`baseline`, `alpha`, `min_queries`, `outcome`, `proposed_overrides`,
`reranker_active` — are additive: enforcement reads only
`profile_sha256`, so pre-existing locks stay valid.
`proposed_overrides` is the winner's overrides dict (`{}` when the
winner carries no recipe). `reranker_active` resolves the winner's
merged config through the real rerank settings loader.

Applying a win is deliberately boring: copy each key of
`proposed_overrides` into `config.yaml` under its block. The values are
consumed at query time; no re-ingest happens. A CLI apply command is
tracked in issue #200. `enforce` mode pins the profile file to the lock
by hash, so a locked profile cannot drift silently.

## 7. The companion PRs (#212–#214)

- **#212 — doctor honesty.** `corpus-kb doctor` reports structured
  embedding dims and reranker state. The llamacpp probe POSTs a sanity
  pair to `{llamacpp_url}/v1/rerank`, because a llama-server on that
  path must start with `--reranking --pooling rank --embedding`; a
  server that answers anything else is misconfigured for reranking.
- **#213 — `transcript_meta` validation mode.** A fifth mode beside
  `off | advisory | enforce | calibrate`: speaker presence, strictly
  increasing sequence numbers, and an empty-unit count. Advisory only —
  it never raises. It runs first in `VALIDATOR_ORDER` because it is the
  cheapest.
- **#214 — ingest runs point at projects.** `ingest_runs` gains a
  nullable `project_id` (migrations 036 Postgres / 037 DuckDB). The
  research transcript link is best-effort and never fails an ingest.
  The SPA Scale page gains a project filter; receipts show the
  validation summary and dedup count; Setup shows dims and reranker.

## 8. Future work

- AutoRAG-HP-style closed-loop tuning of the recipe family
  ([arXiv:2406.19251](https://arxiv.org/abs/2406.19251)) — tracked as
  issue #203. The sweep already produces the per-candidate score rows
  such a loop would consume.
- CLI apply of `proposed_overrides` — issue #200.
- Document-side sweeps: chunker arms through span-anchored labels — the
  plan's Phase 3 goal, tracked as issue #195. The labels shipped; the
  sweep-side arms belong to that issue.
