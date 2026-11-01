# Analysis Method Register (AMR) Program

Status: EXECUTING (approved 2026-10-22, wave-1 dispatch). Branch
`feature/analysis-method-register` off origin/master 28d8631, worktree
`.worktrees/amr`. Scope locked by the owner: FULL COVERAGE — every
machine-implementable method from the 30-method landscape gets an
implementation; only 01/21 (human-led) and 08 (deliberately dropped
LDA, decision recorded in the #137 program) stay register-only.

Zero-drift rules apply in full (see
`docs/research/harness-zero-drift-program.md`). Zero new migrations
(Postgres or DuckDB), zero new env vars, zero new dependencies, zero
lockfile edits.

## 0. Resume fact-recheck (run before resuming after any break)

1. `git worktree list` — `.worktrees/amr` exists on
   `feature/analysis-method-register`.
2. `git -C .worktrees/amr log --oneline -3` — compare with the
   Execution log below. If behind, the wave that was in flight did not
   finish; re-dispatch its brief.
3. origin/master may have moved past 28d8631 (the #127 storage
   program closed at #148; later merges may land). Merge origin/master
   into the branch before the PR if it moved; the program touches
   none of the storage gateway lanes, so conflicts are unlikely.
4. Worktree venv: `.worktrees/amr/.venv` (built 2026-10-22, dev
   extras). Recreate with
   `uv venv .venv && uv pip install -e ".[dev]" --python .venv/Scripts/python.exe`
   if missing.
5. The register source document must exist:
   `C:\Users\moliv\Downloads\Delete\I want to understand the NLP Analysis landscape so.md`
   (30 methods, IDs 01–30; lines 1–464 substantive; tail is footnotes).

## 1. Goals

1. A structured catalogue (the register) of the 30 NLP analysis methods
   from the owner's research document, carrying the document's table
   columns as axes.
2. A selector (`analysis_select_method`) that ranks methods for a
   stated task using the ordinal axes — pure registry logic, no LLM.
3. Full-coverage implementations: 12 new operations plus one new
   composed section, each honest about evidence, each deterministic.
4. Register-as-single-source: the YAML drives the selector, the
   recommendation envelopes, the generated docs table, and the
   harness-card selection block (generator in `poe gen`, drift caught
   by `poe check`).

## 2. Coverage ledger (all 30 IDs land exactly once)

Agent A builds the exact rows from the source document; the mapping it
must honor:

- Already implemented (register rows point at existing ops): BERTopic
  family (09), hybrid search/RRF query (16), ask/RAG (17), extraction
  (19), labeling (23), classification heads (24, approximate),
  SetFit-tier classification (25), and the two ids the document maps to
  existing corpus machinery that Agent A confirms by reading the doc
  (the remaining ids in the 9-sub set recorded in the program memory).
- New ops (this program): 05–07 → `keyphrases_extract` (three
  variants), 10 → topics `seed_terms` (guided), 11 → topics
  `topic_descriptions` (zero-shot), 12 → `clusters_inspect`,
  13 → `centroids_classify`, 14 → `neighbors_classify`, 15 →
  `lexical_classify`, 18 → `nli_classify`, 20 → `active_learn_next`,
  22 → EECS section in `analyze_run`, 26/27 → `sentiment_screen`,
  28 → `contradictions_screen`, 29 → `duplicates_screen`,
  30 → `outliers_rank`.
- Register-only: 01, 21 (`implementation_status: human_led`), 08
  (`not_planned`; LDA dropped).
- Adjacent candidates (Top2Vec, KeyLLM, MMR) appear only in footnote
  slugs of the source: one `adjacent_candidates` note in the register
  header, no rows, no ops.

A conformance test asserts every ID 01–30 appears exactly once.

## 3. Register schema (locked)

Files: `config/analysis_methods.yaml` (repo truth) and
`src/corpus_kb/config/analysis_methods.yaml` (byte-identical packaged
mirror; the package-data glob already covers `config/*.yaml`). Loader:
`src/corpus_kb/analysis/method_register.py` — Pydantic models,
mirrors `ontology.py` (`load_ontology`). Path key:
`analysis.method_register_path` (pre-seeded 2026-10-22 in
`analysis/settings.py`, default `config/analysis_methods.yaml`).

Header keys: `register_version` (semver string, start `1.0.0`),
`source_document` (title + path note), `selection_rules` (the four
priority rules, verbatim short paraphrase), `adjacent_candidates`.

Per-method rows (all keys required unless marked optional):

| Key | Type / vocab | Axis class |
|---|---|---|
| `id` | `"01"`–`"30"` zero-padded | identity |
| `name` | str | identity |
| `summary` | str, ASD-STE100, ≤40 words | descriptive |
| `paradigm` | `supervised\|weak_supervision\|unsupervised\|zero_shot\|human_led` | ordinal |
| `evidence_label` | `direct\|model_based\|preset_composition\|human_judgment` | descriptive |
| `inputs_required` | list of: `raw_text\|embeddings\|labels\|seed_labels\|predictions\|timestamps\|ontology\|llm` | ordinal |
| `calibration_effort` | `none\|low\|moderate\|high` | ordinal |
| `autonomy` | `automated_with_audit\|human_in_loop\|human_led` | ordinal |
| `complexity_tier` | `baseline\|standard\|advanced` | ordinal |
| `selection_priority` | `first\|next\|avoid_first` + `selection_notes` str | ordinal |
| `robustness_caveat` | str | descriptive |
| `bias_risk` | str | descriptive |
| `efficiency_workload` | str, denominators only, never invented timings | descriptive |
| `output_family` | `labels\|themes\|scores\|structures\|text\|errors\|recommendations` | ordinal |
| `output_shape` | str | descriptive |
| `usage_guidance` | str | descriptive |
| `validation_required` | str | descriptive |
| `permitted_claims` | str | descriptive |
| `do_not_claim` | str | descriptive |
| `implementation_status` | `implemented_existing\|implemented_new\|human_led\|not_planned` | identity |
| `op` | op name or null (required when `implemented_*`) | identity |
| `sources` | ≥1 URL (the document's footnote for that method) | descriptive |

Ordinal axes drive the selector rubric. Descriptive axes are surfaced
verbatim in envelopes, docs, and cards — never scored, never ranked
(the source document forbids invented ratings).

## 4. Signature contract (locked; wave-1 code, wave-2 registration)

House handler shape (mirrors `tools/analysis_tools.py`):

```python
async def <op_name>(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    <op-specific params>,
) -> dict[str, object]
```

Every op: `status` first-class; non-success statuses (`disabled`,
`unavailable`, `empty`) carry `reason` + `remedy` strings
(ASD-STE100). `screened_count` (or `counted`) reports the workload
denominator on every op that scans chunks. Floats are rounded to 6
places in envelopes (byte determinism). Typed errors come from
`corpus_kb/errors.py` families only; the integrator adds a family row
only if none fits.

| Op (handler module under `tools/`) | Op-specific params | Success envelope keys |
|---|---|---|
| `analysis_select_method` (`method_select_tools.py`, wave 2) | `task_description: str`, `output_family: str \| None`, `inputs_available: list[str] \| None`, `max_complexity_tier: str \| None` | `status, register_version, recommendations` |
| `keyphrases_extract` (`keyphrase_tools.py`) | `variant: str = "tfidf"` (tfidf\|embedding\|llm), `limit: int = 20`, `chunk_ids: list[str] \| None` | `status, keyphrases, variant, screened_count` |
| `lexical_classify` (`lexical_tools.py`) | `chunk_ids: list[str] \| None` | `status, predictions, screened_count` |
| `neighbors_classify` (`neighbor_tools.py`) | `k: int = 5`, `chunk_ids: list[str] \| None` | `status, predictions, screened_count` |
| `centroids_classify` (`centroid_tools.py`) | `abstain_margin: float = 0.05`, `chunk_ids: list[str] \| None` | `status, predictions, screened_count` |
| `clusters_inspect` (`cluster_tools.py`) | `min_cluster_size: int \| None`, `sample_cap: int \| None` | `status, clusters, noise_count, screened_count` |
| `duplicates_screen` (`vector_screen_tools.py`) | `threshold: float = 0.98`, `limit: int = 100` | `status, pairs, threshold, screened_count` |
| `outliers_rank` (`vector_screen_tools.py`) | `k: int = 10`, `limit: int = 20` | `status, outliers, screened_count` |
| `nli_classify` (`nli_tools.py`) | `labels: list[str]`, `chunk_ids: list[str] \| None`, `limit: int = 50`, `model: str \| None = None` (amendment 1: None → client default; bare `qwen3` is not a pulled tag on the dev machine, so tests pass `qwen3:4b` against recorded fixtures) | `status, predictions, model, screened_count` |
| `contradictions_screen` (`nli_tools.py`) | `candidate_k: int = 5`, `limit: int = 50`, `model: str \| None = None` (amendment 1) | `status, pairs, screened_count` |
| `sentiment_screen` (`sentiment_tools.py`) | `chunk_ids: list[str] \| None`, `limit: int \| None` | `status, predictions, screened_count` |
| `active_learn_next` (`active_learn_tools.py`) | `strategy: str = "margin"` (margin\|entropy\|random_incumbent), `batch: int = 10` | `status, ranked, strategy, requested_batch` |

Amendments locked by the coordinator after wave 1 (wave 2 must honor):
(a) `clusters_inspect` also returns a `params` key (reduction used,
`random_state`, resolved `min_cluster_size` — honesty about which
reduction ran; PCA fallback is honest when umap is absent). The
catalogue row must set `http_extra=("params",)` and `socket_extra=
("params",)` so the parity ALLOWED_EXTRA accepts it. (b) The NLI ops
take an optional `model` override param (see table). (c) Candidate-pair
ties under exact cosine equality in `contradictions_screen` are
deterministic same-machine only (argpartition); full-sort ops
(duplicates, outliers, kNN, centroids) are byte-deterministic. Same
scope bar as the topics engine.

Recommendation rows (inside `recommendations`) carry: `id`, `name`,
`rank`, `rank_reason`, `missing_inputs`, `validation_required`,
`permitted_claims`, `sources`, plus the descriptive axes verbatim.
Selector rubric (fixed order): output_family match → input coverage →
complexity_tier ascending → calibration_effort ascending →
selection_priority (first < next < avoid_first) → id. Deterministic;
golden snapshot test.

EECS section (22): flag `analysis.eecs.enabled` (pre-seeded), delegate
from `analyze_run`, section `eecs` in `analysis/report.py` after
classification; confusion matrix + per-class precision/recall from
stored `chunk_predictions` vs `chunk_labels`; one failing section
never fails the run (the report house rule).

Topics extensions: `seed_terms: list[str]` (guided) and
`topic_descriptions: list[str]` (zero-shot topic list) params on the
topics op path; `zeroshot_min_similarity` exposed in
`topics/settings.py`; `unassigned_count` added to the topics envelope.

Surface names (wave 2): CLI kebab-case of the op name; HTTP
`/api/analysis/<kebab>`; socket + MCP = op name. Exit codes
`(0, 1, 2, 3)`.

## 5. Waves and file ownership (disjoint; no agent commits)

Wave 1 = agents A, B, C in parallel in `.worktrees/amr` (same tree,
disjoint files — the proven middle-distance pattern). Wave 2 = one
integrator. The coordinator commits after each wave (`git add -A` +
single-line conventional commit). Agents never run git commands.

Shared seams PRE-SEEDED by the coordinator (nobody else edits them):
`analysis/settings.py` (register path, eecs, sentiment keys — done),
loader contract `load_analysis_settings(config).eecs/.sentiment/
.method_register_path` (done, verified).

## 6. Wave-1 briefs

### Agent A — Register data

Skills to Read first: engineering-skills `senior-ml-engineer` +
`tdd-guide`; superpowers `test-driven-development` +
`verification-before-completion`.

Deliverables (smallest first):
1. `src/corpus_kb/analysis/method_register.py` — Pydantic
   `MethodRecord`/`MethodRegister` + `load_method_register(config)`
   (path from settings; mirrors `ontology.py` validation style;
   controlled-vocab enforcement with remediation strings) PLUS the
   pure rubric function `rank_methods(register, output_family,
   inputs_available, max_complexity_tier)` implementing the §4
   selector rubric — the ONE implementation; the wave-2 handler and
   the UI both call it.
2. `tests/test_method_register.py` — schema conformance, vocab
   checks, sources ≥ 1, 30 ids exactly once, status↔op consistency,
   rubric ordering + determinism.
3. `config/analysis_methods.yaml` — all 30 rows from the source
   document (read lines 1–360 first; footnotes per method for
   `sources`), header block, STE100 prose.
4. `src/corpus_kb/config/analysis_methods.yaml` — byte-identical
   copy; test asserts byte equality.

Owns ONLY: those four paths. Reads the source document + `ontology.py`
+ `analysis/settings.py`. Does not touch tools/.

### Agent B — Vector-space + NLI methods

Skills: `senior-ml-engineer`, `tdd-guide`, superpowers TDD +
verification. FIRST probe `coding/uncertainty/nli_client.py` against
local Ollama (temp 0, `qwen3`); if Ollama is down, build against the
recorded-fixture pattern and note the deferred live check.

Deliverables:
1. `src/corpus_kb/analysis/vectors.py` — load tenant chunk vectors via
   the existing storage access pattern (read-only; reuse the query
   shape from `classification/`), validate single dimension (mixed
   dims → typed error), guard zero vectors (`norm > 0`), return ids +
   matrix.
2. `tools/vector_screen_tools.py` — `duplicates_screen` (cosine ≥
   threshold; order sim desc, then id pair asc), `outliers_rank`
   (kth-NN distance desc, chunk_id tiebreak).
3. `tools/neighbor_tools.py` — `neighbors_classify` (kNN over labeled
   chunks; vote ties → alphabetical label, then chunk_id).
4. `tools/centroid_tools.py` — `centroids_classify` (per-label
   centroids, cosine, abstain when margin < `abstain_margin`).
5. `tools/cluster_tools.py` — `clusters_inspect` (reuse
   `topics/reduction.py` UMAP+HDBSCAN, `random_state=42`; cluster
   sizes + nearest-centroid chunk examples + noise count).
6. `tools/nli_tools.py` — `nli_classify` + `contradictions_screen`;
   hypothesis template and label order are module constants (sorted
   labels; the ACL 2021 sensitivity rule); lazy `ollama` import (P0
   rule) + AST test; candidate pairs from vector prefilter
   (`candidate_k`), never all-pairs; SHA256-keyed fixtures.

Owns ONLY: those 6 source files + new test files it creates
(`tests/test_vector_screen_ops.py`, `tests/test_neighbor_centroid_ops.py`,
`tests/test_cluster_inspect.py`, `tests/test_nli_ops.py`).
Tests are unit tests with fakes — no Postgres required, no
`requires_postgres` marker.

### Agent C — Text, compose, topics, UI

Skills: `senior-ml-engineer`, `tdd-guide`, superpowers TDD +
verification; `prompt-engineering` for any user-facing card text.

Deliverables:
1. `tools/keyphrase_tools.py` — `keyphrases_extract` (tfidf:
   sklearn `TfidfVectorizer` n-grams scored by max TF-IDF, doc-frequency
   capped; embedding: rank candidate phrases by cosine to the document
   centroid using the existing embedder path; llm: qwen3 prompt via the
   lazy NLI client seam, temp 0).
2. `tools/lexical_tools.py` — `lexical_classify` (TF-IDF + logistic
   head over stored seed labels; reuse the tier-A lazy sklearn seam and
   `threadpool_limits(1)` pattern).
3. `tools/matrix_tools.py` — confusion-matrix + per-class
   precision/recall helpers (pure functions) for EECS.
4. `tools/sentiment_tools.py` — `sentiment_screen` (bundled lexicon
   `config/sentiment_lexicon.yaml` + packaged mirror as labeling LFs →
   existing DS-EM aggregation; envelope names the preset composition
   and carries the register's `do_not_claim` text).
5. `tools/active_learn_tools.py` — `active_learn_next` (margin /
   entropy / random_incumbent seeded 42, over stored
   `chunk_predictions`; score desc, chunk_id asc).
6. EECS section: `tools/analysis_tools.py` `analyze_run` delegation +
   `analysis/report.py` section `eecs` (flag-gated, one-failing-section
   rule).
7. Topics: `seed_terms` / `topic_descriptions` params +
   `zeroshot_min_similarity` in `topics/settings.py` +
   `unassigned_count` in the envelope. Verify guided/zero-shot
   semantics against the installed bertopic source (seed words bias
   the representation; zero-shot falls back to clustering when nothing
   matches).
8. UI: method-advisor panel in `ui/pages.py` + `ui/view_models.py`
   (reads the register, ranks via `rank_methods` — never a second
   rubric; follow the existing ask-page pattern).

Owns ONLY: those files plus `topics/settings.py`, `topics/` engine
edits, and new test files. Never edits `analysis/settings.py`
(pre-seeded). Tests: no Postgres requirement.

## 7. Wave-2 brief — integrator

Skills: `senior-architect`, `tdd-guide`, superpowers verification.

One pass, in this order:
1. Read every wave-1 handler (they exist by now) + the catalogue
   recipe at `docs/research/harness-zero-drift-program.md` lines
   76–84.
2. `contracts/catalogue_analysis.py` — 12 `Operation` rows against the
   real dotted paths; `contracts/__init__.py` OPERATIONS append.
3. `tests/test_surface_parity_deep.py` — LEGS entries with REAL
   callables ×12 (http/socket/mcp specs per the catalogue; MCP adds no
   extra keys).
4. `surface_registry.py` — Surface rows + WRAPPERS ×12; wrapper text
   teaches the axis-walk (research question → output family → inputs
   → calibration/autonomy budget → cautions → run → attach validation
   + permitted claims), generated from the register where possible.
5. `_setup/validate_configs_*.py` VALID_TOOL_NAMES append (the validator
   modules live beside the `validate_configs.py` entry; re-verify the
   file).
6. `tools/method_select_tools.py` — the selector handler: a thin
   envelope wrapper over `rank_methods` (no second rubric) + golden
   determinism snapshot test.
7. `scripts/gen_method_register_docs.py` — register YAML → docs table
   block; append to the `poe gen` target in `pyproject.toml`
   (`gen` ~line 468, `check` ~line 480 — re-verify); run `poe gen`.
8. Adapters: HTTP routes (`api/http.py`, graph routes ~line 172 —
   re-verify), socket arms (`api/_socket_arms.py` —
   re-verify). Mind the P0 eager-catalogue rule: no module-level
   optional imports in any handler chain.
9. `poe verify` + `poe gen` + targeted pytest; fix everything red.

## 7b. (Revoked 2026-10-22) — the owner clarified "no deferment" meant
"ship 100% of scope, punt nothing", NOT more agents. The adversarial
verifier wave is withdrawn; the 4-agent shape (3 wave-1 + 1
integrator + coordinator) stands. The coordinator itself runs the
full-diff review duties inline at gates (envelope honesty, registry
lockstep, lazy-import coverage, register spot-checks) — no agent.

## 8. Failure paths baked into the design

1. BERTopic guided vs zero-shot: seed words bias c-TF-IDF
   representation, never force discovery; zero-shot falls back when
   similarity < `zeroshot_min_similarity`. Expose the threshold;
   report `unassigned_count`.
2. NLI zero-shot sensitivity: hypothesis wording + label order swing
   predictions. Fixed template, sorted label order, temp 0, cached
   fixtures.
3. UMAP/HDBSCAN determinism: HDBSCAN deterministic; UMAP reproduces
   only same-machine with `random_state`. Structural-stability tests
   for cluster ops; exact byte determinism for the numpy ops (dups,
   outliers, kNN, centroids).
4. Active-learning bias toward noisy cases: strategies include
   random_incumbent.
5. P0 lazy imports: any module-level optional import in a handler
   chain crashes extras-less stdio. Function-local imports + AST test.
6. Parity drift: registry-first order; `poe gen` after wrappers; the
   catalogue and LEGS change in the same commit.
7. poe exit codes masked by pipes — capture `$?` explicitly.
8. Mixed-dim embeddings: typed error up front, never a numpy broadcast
   error.

## 9. Definition of done

Scope-completeness check: every deliverable in §6/§7 landed — nothing
moved to a follow-up issue ("no deferment" directive, 2026-10-22).
Mimosa full security scan (re-run — the W0 advisory scan did not
complete) returns a real verdict, not the compatibility fallback.
`poe check` green (verify + gen drift), full
`python -m pytest tests/ -q` green locally, PR opened, all required
CI workflows green (poll to completion; a red CI blocks done). Docs:
`docs/ANALYSIS-METHODS.md` (generated table + selection guide +
"Adding a method" ladder) and a `docs/ANALYSIS.md` append. STE100 on
all new user-facing strings (including the register yaml — the
coordinator runs the STE gate over the yaml prose, not deferred);
ruff + pyright clean.

## Execution log

- 2026-10-22 W0: branch + worktree created @ 28d8631; venv built;
  `analysis/settings.py` seam pre-seeded and import-verified; this
  doc committed. Wave 1 dispatched (A, B, C).
- 2026-10-22 wave 1: A complete + verified (register yaml ×2,
  method_register.py + method_register_rubric.py split per 250-line
  rule, 37 tests; 66/66 with settings regression). B complete +
  verified (vectors.py + nli_contract.py split, 5 ops files, 62
  passed/1 skipped, 16 recorded NLI fixtures via live qwen3:4b probe);
  amendments: clusters_inspect `params` key (parity extra), NLI
  `model` override param (B adding), same-machine tie scope noted.
  Owner directive "all upgrades, no deferment": after clarification
  this means SHIP 100% OF SCOPE, punt nothing — the verifier wave was
  added then REVOKED (§7b); the coordinator runs the review duties
  inline at gates; mimosa full scan + STE-over-yaml are hard gates in
  §9. Register scope stays the verified 30 methods;
  adjacent_candidates (Top2Vec/KeyLLM/MMR) remain note rows (KeyLLM
  is covered by the keyphrases llm variant); 01/21 human_led and 08
  not_planned unchanged (recorded verdicts).
- 2026-10-22 wave 1 SNAPSHOT: committed 7b973ca (57 files, +8291;
  stackdump artifact removed, *.stackdump gitignored). C verified:
  221 passed/1 pre-existing skip across owned files; zero-shot default
  0.7 verified against installed bertopic 0.17.4; venv repaired with
  python-magic-bin (documented fix). Wave 2 integrator dispatched
  (hephaestus) with the full wave-1 inventory + amendments; bertopic
  extra installed into the worktree venv for gate smokes.
