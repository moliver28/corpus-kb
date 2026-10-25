# corpus-kb: Validated Ingestion and Pluggable Chunking — Implementation Plan v2 (surgical)

Document status: DRAFT v2 for user review. NOT approved. Nothing here may be merged until the approval gates in Section 8 are answered.
Written: 2026-10-25. Repo audited: `moliver28/corpus-kb`, branch `master`, HEAD `dcf6f6139f9d97530cf6f71d8c07c9f8f201cc51`.
Reader: an engineer or coding model that understands what corpus-kb is but has NO other context. Read top to bottom. Every term is defined before use. Section 12 is a blocker runbook; Section 13 is a file-by-file change map.

---

## 1. Purpose, scope, and the one-paragraph plan

### 1.1 What the maintainer asked for
1. Make ingestion "industry leading and academic-publication worthy" for a research NLP engine.
2. Each document type (example: interview transcripts for one research project) must be VALIDATED so its data is optimized for retrieval. The check must be judged against that project's research questions (RQs).
3. Validate on a calibration sample first, then APPLY the validated recipe at scale for that document type.
4. Ingestion stays ONE call. The call may optionally run validation. It ALWAYS goes through document type + customization (a "profile").
5. Use current OSS versions. Use non-LLM methods wherever possible. The local LLM harness (Ollama) may help, but must not be the only judge.
6. Support as many chunking options as is sensible, implemented elegantly.
7. The result must hold up to academic scrutiny: rigor, exhaustiveness, accuracy, reproducibility.

### 1.2 The plan in one paragraph
Add a small, read-only VALIDATION stage that runs before the ingest ledger opens. It is configured by an extended profile and writes its report into the existing `ingest_runs.stages` JSONB column (no schema change). Add one `composed` chunking strategy that turns a configurable "segmenter" (semchunk first) into character spans, so chunkers become comparable. Make evaluation labels span-anchored so chunkers can be compared fairly. Add a calibrate-then-lock workflow that picks the best candidate per document type with paired statistics and writes a separate lock file. Everything is off by default, additive, and reversible. Ship in four small phases; each phase is useful alone.

### 1.3 Non-goals
- No rewrite of `run_pipeline`. No removal or rename of any current function, tool, strategy or config key.
- No cloud service, no paid API. No change to stored data for existing documents unless a profile opts in.
- No new infrastructure (no new database, queue or service).

---

## 2. Rules you must follow (project principles and repo law)

### 2.1 Maintainer principles
1. Lean code: about 1% net-new code. Every addition must be user-serving or admin-configurable.
2. OSS-first: use mature, maintained, permissive libraries before writing code. Flag GPL/AGPL unless isolated.
3. No paid APIs or cloud reliance.
4. Preserve ALL user-facing behavior. Never remove, constrain, rename or change it without explicit user permission.
5. Ask before consequential architecture: schema/persistence changes, public API/compatibility changes, new infrastructure, security-boundary changes, irreversible migrations. Section 8 lists every open question.

### 2.2 Repo law (from `AGENTS.md`, "Zero-Drift Program Standards")
- One operation, one handler, many surfaces. CLI, HTTP, socket and MCP are thin adapters. No second pipeline for the same operation.
- Registry-first: edit the parity catalogue (`tests/test_surface_parity_deep.py`) and/or `surface_registry.py` FIRST, then the handler, then regenerate artifacts (`scripts/gen_harness_pack.py`, `cog`, MCP tool-contract snapshot). CI regenerates and diffs.
- One response envelope per operation family. Extra keys per surface only through the parity test's `ALLOWED_EXTRA` map.
- Errors come from `src/corpus_kb/errors.py` (taxonomy). One failure class = one HTTP status + one socket code + one CLI exit code. No catch-all `except Exception` that returns 400.
- Import direction `api -> tools -> handlers -> storage`, enforced by import-linter.
- All user-facing text follows ASD-STE100: short active sentences, 20 words or fewer, simple words. Enforced by Vale (docs) and `pytest -m ste` (Python strings, Flesch-Kincaid grade 9 or lower).
- Source files: 250-line soft limit. Gates: ruff, pyright (no `type: ignore`, no needless `Any`), xenon, vulture, deptry, import-linter.
- Every new gate needs a `poe` target, a CI job, and an enforcement-map row (plus one line in AGENTS.md).
- Deterministic output: stable ordering with tiebreakers.
- Heavy dependencies go into lazy-imported extras that CI never installs (pattern: `latechunk`, `ml`, `topics`, `eval`). Never add to base `dependencies`. Regenerate `licenses.lock.json` with `poe license-lock` when dependencies change.
- Branches: `(feature|bugfix|hotfix|docs|chore|release)/<slug>`, slug = letters, digits, hyphens only. Commits: `feat|fix|docs|chore|test|refactor|perf: Capitalized message`, 10-500 chars, single line (or prefix every body line).
- Honesty rule used across the repo: never invent a number. When data is insufficient, return `insufficient_data` or a `skipped` status with the reason.

### 2.3 Do-not-touch list (breaking these breaks tests or users)
- `PostgresIngestStore.finish_run(run_id, file_hash, stages, errors)` and `fail_run(run_id, error)` signatures. At least five test files fake these exact signatures (`test_pipeline_truth.py`, `test_boundary_adapters.py`, `test_mcp_server.py`, `test_research_ingestion_e2e.py`, and others).
- `TOKEN_BUDGET_CHARS`, `validate_plan` invariants, and the `qa_context` reservation for the `transcript` strategy.
- `ERROR_MAP` numbers in `errors.py`. CLI exit codes are scripted by users (1 = bad input or crash, 2 = total ingest failure, 3 = environment problem).
- MCP graph tools ("do not modify MCP graph tools").
- The existing `relevant_chunk_ids` golden format and `corpus-kb eval run` output.
- Default behavior of `default`, `transcript`, `pptx_structure`, `web_structure` strategies (pinned by tests).

---

## 3. Verified repo facts (read from code at HEAD `dcf6f61`)

### 3.1 The two ingestion paths (this matters most)
corpus-kb has TWO ingestion paths today. The first draft of this plan assumed one.

PATH A — generic documents (`run_pipeline`):
- Entry points in `src/corpus_kb/tools/ingest_tools.py`: `ingest_file`, `ingest_directory`, `ingest_text`, `ingest_url`, `ingest_reembed`, `ingest_status`, `delete_document`. Their `source_type` argument names a REGISTERED STRATEGY (explicit override).
- `tools/ingest_common.py::run_pipeline(text, source_type, path, config, pg_pool, tenant_id, rag_backend, is_file, strategy_name)`: (1) `load_profile(config)` + `with_profile_overrides`; (2) `resolve_strategy` (explicit > profile > config map > detection > default); (3) `strategy.build_chunks` returns a `ChunkPlan`; (4) `validate_plan(plan)` raises `PlanConformanceError` before any write; (5) per-(tenant, content-hash) advisory lock; (6) `_run_locked_stages`: unchanged-probe, `begin_run` (opens an `ingest_runs` row), prepare chunks (hash, provenance, optional contextual blurb), store + embed, shadow hierarchy, entity extraction, statement atomization, optional LlamaIndex mirror, `finish_run`.
- Chunks land in the `chunks` table with vectors. Search uses SQL-side RRF fusion.

PATH B — research transcripts (`research/dynamic_ingest.py`):
- Used by `handlers/research_handler.py` (imports `ledger_record`, `plan_file`, `scan_path`). A code search found no `run_pipeline` or `ingest_text` reference in `research_handler.py`, so research transcripts appear NOT to go through `run_pipeline`. (Confidence: high but the file was not read in full. See V1.)
- Supported suffixes: `.txt .md .vtt .srt .csv .docx`. Dedup uses two keys: `file_sha256` of raw bytes (the `ingested_files` ledger) and `text_sha256` of normalized turn text (content-addressed turn store). Turns are parsed by `research/transcript_parser.py::parse_transcript` and stored with `store_turn_texts` into research projections.
- Research retrieval and evaluation work on research UNITS (`unit_id`), not `chunks`: see `research/retrieval.py` and `research/retrieval_eval.py` (arms vector, FTS, RRF, RRF+rerank; labels only from human-reviewed `research_assignments` with status `confirmed` or `overridden`).

CONSEQUENCE: the user's headline example (interview transcripts for a research project) most likely lives on PATH B, where the retrieval unit is a turn/unit and chunkers do not apply the same way. Section 4.2 resolves this with a decision gate before any chunking work.

### 3.2 Profiles (the unit of customization)
- Code: `src/corpus_kb/profiles/loader.py` (11.5 kB, so it is likely already over the 250-line soft limit), `profiles/seeds.py`, `profiles/__init__.py`. Gate: `src/corpus_kb/_setup/validate_profiles.py` (`poe validate-profiles`).
- Strict schema: unknown top-level keys FAIL. `KNOWN_TOP_KEYS = {name, description, data_type, ingestion, extraction, templates, seeds_file}`. `KNOWN_INGESTION_KEYS = {strategies, default}`. `KNOWN_EXTRACTION_KEYS = {mode}`.
- `DATA_TYPES = {interview, focus_group, survey, news, mixed}`. These five ARE the "document types" in this plan.
- `IngestionProfile` is a frozen dataclass: `name, data_type, description, strategies, default_strategy, extraction_mode, seeds_file, templates`.
- Active profile is chosen by config key `ingestion.profile` (single, global). `load_profile(config, override=None)` supports a per-call `override`, but `run_pipeline` calls `load_profile(config)` WITHOUT an override today.
- Errors use `ValidationError(message, remediation=...)`. Paths use `safe_profile_path` (blocks escapes from the profiles directory).
- `validate_profiles` globs `profiles/*.yaml` and skips `*.seeds.yaml`. A `*.lock.json` file is therefore ignored by the gate today.
- Only one profile ships: `profiles/starter-interview.yaml` (`data_type: interview`, `ingestion.default: transcript`, `extraction.mode: few_shot`, four templates).

### 3.3 Ledger
- `ingest_runs` (Postgres migration 022): `stages JSONB NOT NULL DEFAULT '{}'`, status in (`running`, `completed`, `failed`), plus `error TEXT`. A validation report can ride in `stages` with NO schema change (Postgres confirmed; the DuckDB mirror in `migrations_duckdb/022_ingest_integrity.sql` is unverified, see V5).

### 3.4 Strategy contract and registry
- `ingestion/base.py`: `IngestionStrategy` Protocol (`detect`, `build_chunks`), `ChunkPlan`, `PlannedChunk` (has `source_start_char`, `source_end_char`, `heading_path`, `metadata`), `validate_plan`.
- `ingestion/registry.py`: built-ins `default`, `pptx_structure`, `transcript`, `web_structure` registered in `_ensure_ready`; third-party plugins via entry-point group `corpus_kb.ingestion_strategies` (error-tolerant). Detection runs for provenance on every document.
- `DefaultStrategy.build_chunks`: Unstructured partition (files) or single-element wrap (raw text), then `chunking/unstructured_chunker.py::chunk_elements`. Output is pinned by test. Whether `chunk_elements` still dispatches tree-sitter code chunking is unclear (AGENTS.md says yes; `pyproject.toml` comments say the code chunker no longer exists).

### 3.5 Evaluation that already exists
- `research/retrieval_eval.py`: recall@k, MRR, nDCG@10, p95 latency over four arms; `recommend_default` only recommends when measured.
- `corpus-kb eval run --golden golden.jsonl`: chunk-ID goldens; DeepEval answer metrics with a local Ollama judge (`[eval]` extra).
- Also present: `research/promotion.py` (promotion scorer), MAPIE cross-check (tests only), Bayesian beliefs (W4), extraction tune (W5).

### 3.6 Gaps this plan fixes
- G1 No validation stage and no per-document-type retrieval-fitness thresholds.
- G2 Profiles carry no chunking, validation or research-question information. Only one profile ships.
- G3 Few chunking methods and no way to compare them.
- G4 Chunk-ID goldens cannot compare chunkers (IDs change with the chunker).
- G5 The size guard is a chars/4 heuristic, not a real tokenizer count.
- G6 No significance tests, no confidence intervals, no standard qrels/run export.
- G7 No near-duplicate screen; no record of which locked recipe produced a run.
- G8 Two ingestion paths; validation must not silently skip PATH B.

---

## 4. Verify-first tasks and decision gates (do before writing code)

### 4.1 Verification register
Status: R = resolved in this audit, P = partly resolved, O = open.

| ID | Question | Status | Finding or how to resolve |
|---|---|---|---|
| V1 | Does PATH B (`dynamic_ingest` via `research_handler`) bypass `run_pipeline`? What does it chunk into? | P | Appears to bypass it (no `run_pipeline`/`ingest_text` hit in `research_handler.py`). Read `research_handler.py`, `domain/transcript.py`, `projections/research/_common.py::store_turn_texts` to confirm and to learn the unit shape. |
| V2 | Profile schema and loader | R | See 3.2. Strict keys; frozen dataclass; `override` exists but is unused by `run_pipeline`. |
| V3 | What does the default strategy do? | P | See 3.4. Read `chunking/unstructured_chunker.py` to settle the code-chunker doc drift. |
| V4 | Can a calibration sweep run on a scratch DuckDB engine? | O | Test: `corpus-kb setup --backend duckdb` in a temp dir, then ingest `docs/demo-corpus`. Check the single-writer file lock and `database.backend` switching. |
| V5 | Can the report ride in `ingest_runs.stages`? | P | Postgres: yes (JSONB). Check `migrations_duckdb/022_ingest_integrity.sql` column type and `finish_run` serialization in `tools/ingest_store.py`. |
| V6 | Does Pandera core pull pandas? | O | Pandera 0.35.0 [web:27]. DEMOTED in v2 (Section 6). Resolve only if validators outgrow plain Python. |
| V7 | ranx 0.3.21: which paired tests and corrections? Windows/numba install? Qrels/Run `save` formats? | O | Install in a clean venv on Python 3.11 and 3.13, Ubuntu and Windows. List `ranx.compare` options. Needed only in Phase 4. |
| V8 | Who uses the generic golden format? | O | Read `cli_eval.py`, `eval/`, `docs/EVALUATION.md`. Constrains the additive span format. |
| V9 | Does Presidio coexist with the spaCy C-stack pins? | O | DEFERRED in v2. Skip unless PII screening is requested. |
| V10 | Where is `surface_registry.py`, and which tests pin the MCP tool-contract snapshot? | O | A path search for `surface_registry.py` and for `ingest_file` in the parity test returned nothing under the guessed paths. Run `git grep -n "surface_registry"` and `git grep -n "ALLOWED_EXTRA"`. |
| V11 | Which index answers research queries: `chunks` (PATH A) or research units (PATH B)? | O | Read `research/retrieval.py` (`_unit_arms`). This decides Section 4.2. |
| V12 | Which tokenizer matches the embedding model, and can it load offline? | O | Default model is Ollama `nomic-embed-text` (768d). Ollama exposes no tokenizer. A Hugging Face tokenizer needs a pre-pulled local file. If absent, keep chars/4 and record `tokenizer_basis: heuristic` in every report. |
| V13 | Does a project-to-profile mapping already exist (W5 "project profiles")? | O | Search `ingestion.profile`, `project`, `profile` in `research/` and `handlers/`. Decides whether a per-call `profile` argument is needed. |
| V14 | Size of the codebase (for the 1% budget) | O | Count source lines under `src/corpus_kb/` (for example `find src -name "*.py" | xargs wc -l`). Record in the PR. |

Record every V-result in the PR description. A result that contradicts this plan STOPS work and returns to the maintainer.

### 4.2 Decision gate G0: where do chunkers apply? (resolve V1 + V11 first)
- Case A — research queries run on research units built by `parse_transcript`/the unit store (PATH B): chunk segmenters do NOT change research retrieval. Then (i) the validation stage and span-anchored labels apply to units; (ii) the `composed` strategy serves PATH A only (documents, slides, web, notes); (iii) for transcripts the "segmenter" question becomes how turns are grouped into units, and any change there is a schema/behavior change that needs explicit approval. Phase 2 shrinks to PATH A.
- Case B — research queries also search `chunks` from `run_pipeline`: the plan applies unchanged to both.
- Do not guess. The answer changes Phase 2 scope.

---

## 5. Target design

```
ingest call  (ingest_file | ingest_directory | ingest_text | ingest_url; PATH B via research handler)
   optional additive args: validate = off | advisory | enforce | calibrate     (default off)
                           profile  = <name>                                    (default: config ingestion.profile)
        |
   [PATH A] run_pipeline: load_profile(override) -> resolve_strategy -> build_chunks -> validate_plan
        |                    ^ "composed" strategy (Phase 2): Segmenter -> spans -> PlannedChunk
        v
   VALIDATION STAGE (Phase 1; read-only; runs BEFORE advisory lock and begin_run)
        |  -> report dict
        v
   unchanged: lock -> begin_run -> store/embed -> extract -> atomize -> finish_run(stages + {"validation": report})

   [PATH B] the same validation function is called on units before the ledger write (shared code, no new pipeline)
```

### 5.1 Design rules that keep it surgical
1. One validation entry function: `corpus_kb.validation.run(subject, profile, mode) -> ValidationReport`. `subject` is a small dataclass holding spans/texts plus metadata, built by a thin adapter per path. No pipeline logic in surfaces.
2. Validators are a registry of plain functions `(subject, settings) -> Finding list`. Adding one = one function + one config key.
3. Read-only and pre-ledger: validation writes nothing. A failure leaves no partial rows (same pattern as `PlanConformanceError`).
4. Spans first: every chunk/unit carries `(source_start_char, source_end_char)` on the canonical `document_text`.
5. Config over code: thresholds, segmenters and candidates come from the profile. No hard-coded thresholds in code.
6. Fail honest: insufficient data, degraded embeddings and missing extras produce `skipped` with a reason, never a fabricated score.

### 5.2 Profile extension (additive; all new keys optional)
The loader is strict, so new keys must be registered in `KNOWN_TOP_KEYS`. Put new parsing in a NEW file `profiles/blocks.py` (loader.py is already large) and call it from `parse_profile`. Add optional fields with defaults to `IngestionProfile` (still a frozen dataclass; existing constructor calls keep working).

```yaml
# profiles/<name>.yaml — new keys are optional
name: interviews-projectX
data_type: interview                 # one of: interview focus_group survey news mixed
ingestion: {default: transcript}
chunking:                            # Phase 2; PATH A only unless G0 says Case B
  segmenter: semchunk                # semchunk | element | turn | exchange | window_k_turns | (later: chonkie names)
  size: {tokens: 512, floor: 200, overlap: 0.10, tokenizer: auto}   # auto = embedder tokenizer if loadable, else chars/4
  candidates: [semchunk, exchange, element]      # calibration sweep only
research_questions: projectX.rq.yaml  # file inside the profiles dir; resolved by safe_profile_path
validation:
  mode_default: off                  # off | advisory | enforce
  on_validator_error: warn           # warn | fail   (enforce always fails closed)
  structural: {min_chunks: 1, max_empty_ratio: 0.0}
  lossless: {min_coverage: 0.995}
  dedup: {minhash_threshold: 0.9, num_perm: 128, seed: 1}
  retrieval: {k: 10, self_retrieval_sample: 200, self_retrieval_min_hit1: null, min_recall_at_k: null}
```
All numbers above are PLACEHOLDERS. Real thresholds come from calibration, never from this document. `null` means "report only; do not gate".

Research question file (new, small): list of `{id, text}` plus optional `gold` entries `{doc, start, end}` or `{unit_id}`. It is the pre-registration artifact for publication and its hash goes into the lock.

### 5.3 Lock file (separate from the authored profile)
- `profiles/<name>.lock.json`, written only by `validate=calibrate`. Never edit the authored YAML automatically.
- Contents: `profile_sha256`, `rq_sha256`, chosen segmenter, size settings, metrics with confidence intervals, candidate table, tool versions (corpus-kb git SHA, `uv.lock` hash, semchunk/datasketch/ranx versions), embedding model name + digest, tokenizer basis, seeds, date, and the calibration sample manifest (path + SHA-256 per file).
- `enforce` mode compares the current `profile_sha256` to the lock and fails on mismatch.
- `*.lock.json` is not matched by the `*.yaml` glob in `validate_profiles`, so add an explicit lock check there (Phase 3).

### 5.4 Stage ordering relative to the ledger
Validation runs after `validate_plan` and before `advisory_lock_ctx`. Reasons: it is read-only; the lock is per content hash and should not be held during slow checks; a failure must not create an `ingest_runs` row. Result keys added only when `validate != off`: `validation` (summary) in the envelope, and `stages["validation"]` (full report) in the ledger. Declare any new envelope key in `ALLOWED_EXTRA`.

---

## 6. OSS decisions (re-audited; versions checked 2026-10-25)

v2 cuts dependencies hard. Rule: add a library only where it replaces non-trivial custom code.

| Tool | License | Latest seen | v2 decision | Reason |
|---|---|---|---|---|
| datasketch | MIT | 2.0.0 [web:31] | ADOPT Phase 1 (`[validate]` extra) | MinHash LSH is non-trivial. v2 changed default hashing: pin `>=2.0,<3`; never reuse persisted sketches across versions. |
| semchunk | MIT | 4.1.1 [web:56] | ADOPT Phase 2 (`[chunking]` extra) | Native char offsets; tiny; tokenizer-agnostic. Pin `>=4.1,<5` (4.0.0 shipped Mar 2026). Never enable its AI/Isaacus mode (cloud). |
| ranx | MIT | 0.3.21 [web:26] | ADOPT Phase 4 only (`[validate]`) | Paired tests, fusion, LaTeX output. About 14 months since last release; isolate behind one function. Confirm via V7. |
| Chonkie | MIT | 1.7.0 [web:55] | DEFER to Phase 5 (`[chunking]`) | semchunk + existing turn/exchange/element cover v1. Add Chonkie families only after the sweep shows a gap. No `TeraflopAIChunker` (cloud). `SlumberChunker` only with local Ollama and explicit approval. |
| Pandera | MIT | 0.35.0 [web:27] | DEMOTE (v1 uses plain Python predicates) | Frame validation needs a DataFrame; checks needed are about 30 lines. Revisit only if V6 shows no pandas pull and checks grow. |
| ir-measures | Apache-2.0 | 0.4.3 [web:29] | DROP | TREC qrels/run files are trivial text; ranx can read and write them (verify in V7). |
| Presidio | MIT | 2.2.364 [web:30] | DEFER | PII flagging is out of the stated goal; spaCy C-stack pins make CI risky (V9). |
| Great Expectations | Apache-2.0 | 1.24.1 [web:32] | REJECT | Heavy, data-store oriented. |
| Ragas | Apache-2.0 | 0.4.3 [web:28] | REJECT | Duplicates DeepEval; collects usage data unless opted out. |
| krippendorff | GPL-3.0+ | 0.9.0 [web:34] | REJECT | Copyleft; find a permissive option only if agreement stats are requested. |
| MTEB | Apache-2.0 | 2.26.21 [web:33] | DEFER | Only for embedder-comparison papers. |

Evidence behind the chunking design (cited in the first draft; keep when writing docs): no chunker wins everywhere; structure-based methods often match LLM-guided ones for corpus-wide retrieval (ACM taxonomy study, doi 10.1145/3805712.3808575); contextual retrieval vs late chunking trade coherence against cost (arXiv 2504.19754); semantic chunking can create tiny fragments, so enforce a minimum size; compare candidates with paired tests and multiple-comparison correction.

---

## 7. Phased work packages

Budget summary (source lines only; tests and docs excluded; estimates, not measurements):

| Phase | Work | Est. LOC | New deps |
|---|---|---|---|
| 0 | Verify, baseline, decisions | 0 | none |
| 1 | Validation core + report + doctor checks | about 190 | datasketch (extra) |
| 2 | Profile blocks + `composed` strategy (semchunk) | about 180 | semchunk (extra) |
| 3 | Span labels + calibrate/lock | about 150 | none |
| 4 | Statistics + publication report | about 70 | ranx (extra) |
| 5 | Deferred options (Chonkie families, PII, LLM tier) | not budgeted | on request |
Total Phases 1-4: about 590 lines. This exceeds the 1% target unless the codebase is above about 59k lines (V14 decides). If it does not fit, cut Phase 4 reporting first, then the `window_k_turns` and `exchange` segmenter wrappers.

### Phase 0 — Verify, baseline, decide (no code)
Steps:
1. Complete V1, V3-V5, V10-V14 and Gate G0 (Section 4.2).
2. On a clean checkout run `uv sync`, then `poe verify` and `poe gen`. Record pass/fail and timings.
3. Baseline retrieval: ingest `docs/demo-corpus`, then run `corpus-kb eval run --golden <demo golden> --k 10 --report baseline.json`. Commit the baseline under `findings/`.
4. Collect answers to D1-D8 (Section 8).
Exit criteria: V-results written; G0 answered; baseline stored; approvals recorded.
Rollback: not applicable.

### Phase 1 — Validation core (read-only, off by default)
Needs: D1, D2, D5, D8.
Files (new): `src/corpus_kb/validation/__init__.py` (public `run`, about 40 lines), `validation/types.py` (dataclasses `Subject`, `Finding`, `ValidationReport`, about 40), `validation/checks.py` (structural, lossless, dedup, about 90), `validation/adapters.py` (build `Subject` from a `ChunkPlan`; later from units, about 20).
Edits (existing): `tools/ingest_common.py` (one call site after `validate_plan`, about 8 lines; do NOT change `finish_run`; merge the report into the `stages` dict), `tools/ingest_tools.py` and the surface adapters (additive `validate` argument), `_setup/` doctor section (about 20 lines, read-only).
Steps, in this order (registry-first):
1. Update the parity catalogue / `surface_registry.py` for the new optional `validate` argument. Regenerate the MCP snapshot and harness pack.
2. Write failing tests first (Section 10).
3. Implement `checks.py`. Checks:
   - structural: indices contiguous, non-empty text, required metadata keys per `data_type`, chunk length versus the size guard (tokenizer basis recorded);
   - lossless: union of `(start, end)` spans covers at least `min_coverage` of `document_text`, with overlap/gap statistics;
   - dedup: MinHash LSH on normalized text; near-duplicate pairs within the batch and against same-tenant documents (query via the gateway under `tenant_connection`; cap the lookup);
   - transcript-only metadata: speaker present, timestamps monotonic, orphan turns (read from existing parse results; never re-parse).
4. Implement `validation.run`: validators run in a fixed order; each is isolated (a crash becomes `Finding(status="error", cls=type(exc).__name__, message=...)`); output is deterministic.
5. Mode semantics: `off` returns immediately (byte-identical envelope); `advisory` attaches the report and never blocks; `enforce` raises `ValidationError` with a remediation when any gated check fails or a validator errors (fail closed).
6. Wire the doctor section: extras importable, `datasketch` version in range, profile parses, lock hash matches (when present).
Acceptance:
- With `validate=off` the result envelope and stored rows are byte-identical to before (snapshot tests).
- With `advisory` on `docs/demo-corpus` the report is stable across two runs (same JSON).
- A bad fixture (gaps in coverage, duplicate document, non-monotonic timestamps) produces the expected findings.
- Extras-less CI jobs stay green; `poe verify` and `poe gen` green.
Rollback: revert the PR. No schema or data change. Remove the `[validate]` extra if desired.

### Phase 2 — Profile blocks and the `composed` strategy
Needs: G0, D3 (and D4 if Chonkie is brought forward).
Files (new): `profiles/blocks.py` (parse/validate the new keys with remediation text, about 60 lines), `ingestion/strategies/composed.py` (about 80), `ingestion/strategies/segmenters.py` (semchunk wrapper, offset-preserving turn/exchange/window wrappers, about 40). Keep each file under 250 lines.
Edits: `profiles/loader.py` (add keys to `KNOWN_TOP_KEYS`; call `blocks.parse_*`; add optional dataclass fields; about 15 lines), `_setup/validate_profiles.py` (cross-check segmenter names, RQ file existence, about 20 lines), `ingestion/registry.py::_ensure_ready` (register `composed`, 2 lines), `pyproject.toml` (extras, DEP002 entries).
Steps:
1. Add parsing + cross-reference tests for the new keys (unknown segmenter, missing RQ file, bad numbers each give a `ValidationError` with a fix).
2. `composed.detect()` returns `DetectResult(0.0, "profile-selected")` unless the profile selects it. It never steals documents from other strategies.
3. `composed.build_chunks()`: read `profile.chunking`; get spans from the segmenter; build `PlannedChunk(text=raw[start:end], source_start_char=start, source_end_char=end, ...)`; enforce `floor` (merge short tails; never truncate); keep indices continuous; record `tokenizer_basis` in `plan.warnings` when heuristic.
4. Segmenter contract: `(text: str, spec: SegmenterSpec) -> list[tuple[int, int]]`. Spans must be sorted, within bounds and non-empty.
5. Offset invariant: `raw[start:end] == chunk.text` for offset-native segmenters (semchunk, element mapping where available). Where a segmenter cannot return offsets, `composed` refuses it with a clear error; do not fabricate offsets.
Acceptance:
- `validate_plan` passes for every segmenter; property-style parametrized test confirms the offset invariant.
- Existing strategies' outputs unchanged (syrupy snapshots).
- A profile with no `chunking:` block behaves exactly as before.
Rollback: unregister `composed`; remove the extra. Documents ingested with `composed` keep working (they are ordinary chunks).
Risk: re-ingesting under a new segmenter changes `chunk_hash` and chunk IDs by design. Opt-in per profile only.

### Phase 3 — Span-anchored labels and calibrate-then-lock
Needs: D6, D7, V4, V7 (partly).
Files (new): `validation/labels.py` (convert span/unit labels to chunk IDs by overlap, about 50), `validation/calibrate.py` (sweep + lock writer, about 70), `validation/retrieval.py` (self-retrieval + recall using existing arms, about 30).
Edits: `research/retrieval_eval.py` stays the single source of metric math (import it; do not copy). `cli_eval.py` accepts the additive span format; the old format is unchanged.
Steps:
1. Golden format extension (additive): `{"query", "relevant_spans": [{"doc","start","end"}]}` or `{"query", "relevant_units": [unit_id,...]}`. Old `relevant_chunk_ids` keeps working. `corpus-kb eval build-golden --validate` accepts all three and names line numbers on error.
2. Overlap mapping: a chunk is relevant if its span overlaps a labeled span by at least a configured fraction (default to be set in calibration; placeholder 0.5). Record the rule in the report.
3. `validate=calibrate` flow: (a) require the profile, `research_questions` and a sample path; (b) build a scratch DuckDB engine in a temp directory (V4); (c) for each candidate, ingest the sample into scratch, run the four retrieval arms with span labels, collect per-query metrics; (d) refuse to score if embeddings are degraded or all-zero (use the existing `degraded` signal); (e) write `<name>.lock.json` plus a proposed profile diff; never overwrite the authored YAML; (f) delete the scratch directory in a `finally`.
4. `enforce` compares `profile_sha256` to the lock.
5. If labels are missing, calibration reports `insufficient_data` and still runs the non-LLM structural checks.
Acceptance:
- The same labeled spans give consistent recall@k across two chunkers (test with two fixtures).
- Calibration twice with the same inputs produces identical lock content except the timestamp.
- After editing the profile, `enforce` fails with a plain-English message naming the changed hash.
- The scratch directory is gone after success, failure and cancel.
Rollback: delete `*.lock.json`; set `mode_default: off`.

### Phase 4 — Statistics and publication report
Needs: V7.
Files (new): `validation/report.py` (about 70 lines).
Steps: paired comparison across candidates on the same queries using ranx; confidence intervals; multiple-comparison correction (use what ranx exposes; otherwise state the limitation); LaTeX tables; TREC-format qrels and run export; JSON report with manifest and versions.
Acceptance: the report regenerates from the lock + artifacts on another machine; a human can rerun every number.

### Phase 5 — Deferred options (not budgeted; only on request)
Chonkie families (token, sentence, semantic, neural, table), local-LLM `slumber` chunker (explicit approval), PII flagging, an LLM-assisted tier. Rules if built: LLM output is tagged `silver`, never mixed into headline metrics, local Ollama only, off by default.

---

## 8. Approval gates and open decisions (answers pending; do not cross a gate without one)

| ID | Decision | Recommendation | Alternatives | Gates |
|---|---|---|---|---|
| D1 | Persist reports in `ingest_runs.stages` JSONB plus a JSON artifact (zero schema change) | Yes. Postgres column confirmed JSONB; verify DuckDB (V5) | New table `ingest_validation_reports` (queryable; changes both migration sets, parity gate, rollback script) | Phase 1 |
| D2 | Default `validate` mode | `off` everywhere until a document type is calibrated; then `advisory` | `advisory` from day one; `enforce` for locked profiles | Phase 1 |
| D3 | Chunking adapters | Direct semchunk wrapper (native offsets), Chonkie later | LlamaIndex node parsers only (no new direct dep, weaker offsets) | Phase 2 |
| D4 | Local-LLM `slumber` chunker | Exclude | Include as opt-in sweep candidate | Phase 5 |
| D5 | Additive optional `validate` and `profile` arguments on the ingest tools (public API and MCP snapshot change) | Approve; backward compatible; needed for "one call" | Separate `ingest validate` tool (second entry point, violates "one call") | Phase 1 |
| D6 | Additive span/unit golden format | Approve; old format untouched | Keep chunk-ID only (blocks fair chunker comparison) | Phase 3 |
| D7 | Calibration runs on a scratch DuckDB engine inside the same handler, available on every surface | Approve; if MCP time limits bite, add `max_docs` | CLI only (breaks surface parity unless exempted) | Phase 3 |
| D8 | Cover PATH B (research transcripts) with the same validation function through a thin adapter, WITHOUT merging the two pipelines | Approve | Leave PATH B uncovered (misses the main use case); unify the pipelines (large consequential change; not recommended now) | Phase 1 |

Out of scope unless the maintainer opts in: re-chunking existing documents; changing how turns become research units; any default-behavior change.

Question for the maintainer, in the order that unblocks work: (1) answer G0/V11 (do research queries search `chunks` or units?); (2) approve D5 and D8; (3) confirm D1; (4) choose D3.

---

## 9. Evaluation protocol for publication-grade claims

1. Pre-register: freeze the RQ file and the candidate list before the sweep; store their hashes in the lock.
2. Labels: human-reviewed only (`confirmed` or `overridden` assignments, or hand-marked spans). Any LLM-produced label is `silver` and reported separately.
3. Sample size: do a power analysis for the smallest effect that matters; treat small label sets as exploratory and say so.
4. Metrics: recall@k, MRR, nDCG@10 (existing functions), p95 latency, index size.
5. Statistics: paired comparison on identical queries, confidence intervals, multiple-comparison correction.
6. Ablations: one factor at a time first (segmenter, size, overlap, enrichers, arm).
7. Reproducibility: fixed seeds, `uv.lock` hash, Ollama model digest, corpus manifest with SHA-256 per file, config hash, corpus-kb git SHA.
8. Artifacts: JSON report, TREC qrels/run, LaTeX tables, lock file.
9. Limitations section: sample size, single embedder, domain specificity, silver share, tokenizer basis.

---

## 10. Test plan (write tests first)

New files mirror `src/`:
- `tests/validation/test_checks.py`: each check has a pass fixture and a fail fixture. Include empty document, one-chunk document, Unicode edge cases, overlapping spans, gaps, duplicate documents, non-monotonic timestamps.
- `tests/validation/test_run_modes.py`: `off` byte-identical; `advisory` never raises; `enforce` raises `ValidationError` with remediation; validator crash isolation; deterministic JSON (two runs equal).
- `tests/profiles/test_blocks.py`: unknown segmenter, missing RQ file, bad types, legacy profile unchanged, `starter-interview.yaml` still valid.
- `tests/ingestion/test_composed.py`: conformance via `validate_plan`; offset invariant; `floor` merging; continuous indices; refusal of offset-less segmenters.
- `tests/validation/test_labels.py`: span-to-chunk mapping with two chunkers; threshold edge cases.
- `tests/validation/test_calibrate.py`: deterministic lock; scratch cleanup on success, failure and cancel; degraded-embedding refusal; `insufficient_data` path.
- Parity: `tests/test_surface_parity_deep.py` catalogue entry for the new arguments; MCP snapshot regenerated.
- Existing suites that must stay green unchanged: `test_pipeline_truth.py`, `test_boundary_adapters.py`, `test_mcp_server.py`, `test_research_ingestion_e2e.py`, strategy snapshot tests, `test_plain_language.py` (`pytest -m ste`).
Rules: no live Ollama in CI (recorded fixtures or fakes, as the repo does); extras-less CI jobs must pass (lazy imports, `pytest.importorskip` for extras tests); Windows and macOS legs matter (see runbook B-07, B-08). Use parametrized cases instead of adding a property-testing dependency.
Gates to add (rule 10): `poe validation-gate` (runs the validation tests), a CI job calling it, an enforcement-map row, one line in AGENTS.md.

---

## 11. Preventative error handling (design requirements)

Use only the existing taxonomy (no new error classes, no `ERROR_MAP` change): `ValidationError` (bad profile or failed gate), `ConfigError` (bad `config.yaml`), `DependencyMissingError` (missing extra), `ConflictError` (lock/hash drift), `DatabaseUnavailableError`, `NotFoundError`. Each message is plain English, 20 words or fewer, with a `remediation=` that names one fix. Do not add `except Exception -> 400` handlers on surfaces.

| ID | Failure | Prevention or detection | Behavior | Error class and fix text |
|---|---|---|---|---|
| E1 | Extra not installed (`datasketch`, `semchunk`, `ranx`) | Lazy import inside the function; doctor check | Skip that check in `advisory` with reason; fail in `enforce`/`calibrate` | `DependencyMissingError`: "Install the validate extra: pip install 'corpus-kb[validate]'." |
| E2 | Profile has unknown key or bad value | Strict parse in `profiles/blocks.py` + `poe validate-profiles` in CI | Fail before any write | `ValidationError` naming the key and allowed values (house pattern) |
| E3 | RQ file missing or unsafe path | `safe_profile_path`; existence check in `validate_profiles` | Fail before any write | `ValidationError` with the profiles directory path |
| E4 | A validator raises | Per-validator isolation | `Finding(status="error")`; `advisory` continues; `enforce` fails closed unless `on_validator_error: warn` is set AND mode is `advisory` | `ValidationError` in enforce: "Validator X failed. See the report. Fix the cause, then retry." |
| E5 | Embeddings degraded or all-zero (Ollama down; zero-vector fallback) | Reuse the `degraded` flag and `_all_zero` helper | Retrieval metrics `skipped` with reason; never score zeros | none in advisory; `DatabaseUnavailableError`-style remediation text in calibrate: "Start Ollama and pull the embedding model." |
| E6 | No labels (no reviewed codings, no gold spans) | Count labels first | `insufficient_data`; structural checks still run | none |
| E7 | Lock mismatch (profile edited after calibration) | Compare `profile_sha256` | `enforce` refuses | `ConflictError`: "The profile changed after calibration. Run validate=calibrate again." |
| E8 | Segmenter returns bad spans (overlap, out of range, empty) | Span validator inside `composed` + `validate_plan` | Fail before any write | `PlanConformanceError` (existing) |
| E9 | Tokenizer cannot load offline | Try-load; fall back | Use chars/4; record `tokenizer_basis: heuristic` in every report | none (warning in `plan.warnings`) |
| E10 | Very large corpus (memory, time) | Caps: `max_docs`, `self_retrieval_sample`, MinHash `num_perm`; stream where possible | Over cap: sample deterministically (seeded) and record `sampled: true` | none |
| E11 | Concurrency (same content ingested twice) | Validation is before the lock and read-only | Safe; the existing advisory lock still serializes writes | existing `skipped/concurrent-run` |
| E12 | Cancel or crash during calibrate | `try/finally` removes scratch dir; lock written atomically (temp file then rename) | No partial lock file | none |
| E13 | DuckDB single-writer file lock | Use a unique temp path per run | No clash with the main database | `DatabaseUnavailableError` text if the file is locked |
| E14 | Tenant isolation | All queries through `tenant_connection`; no cross-tenant dedup | RLS preserved (Postgres); explicit tenant predicate (DuckDB) | none |
| E15 | Cross-platform path or line-ending issues | `pathlib` everywhere; open files with `encoding="utf-8"`; normalize `\r\n` only in a copy used for hashing, never in stored text | Offsets refer to the canonical `document_text` only | none |

Logging: use the existing logger and OpenTelemetry spans (`utils/tracing.py`). Add one span `validation` with attributes `mode`, `status`, `finding_count`, `sampled`. Never log document text.

---

## 12. Blocker runbook (symptom → likely cause → quick check → fix)

Put the same table in a new `docs/VALIDATION.md` (STE English, Vale clean). Link it from `docs/INGESTION.md` and from every remediation hint.

| ID | Symptom | Likely cause | Quick check | Fix |
|---|---|---|---|---|
| B-01 | `poe validate-profiles` fails: "unknown keys: chunking" | `KNOWN_TOP_KEYS` not extended or old install | `python -c "from corpus_kb.profiles.loader import KNOWN_TOP_KEYS as k; print(sorted(k))"` | Add the new keys in `loader.py`; reinstall with `pip install -e .` |
| B-02 | Parity or MCP-snapshot test fails after adding `validate` | Registry or snapshot not regenerated | `poe gen`; compare diff | Update catalogue first, then `scripts/gen_harness_pack.py`, regenerate snapshot, commit generated files |
| B-03 | `lint-imports` fails | `validation/` imported from `api/` or `tools/` imports upward | Read the failing contract name | Keep `validation` below `tools`; surfaces call the handler only |
| B-04 | File over 250 lines or xenon complexity fails | Logic added to `loader.py` or `ingest_common.py` | `wc -l`; `poe complexity` | Move code to `profiles/blocks.py` or `validation/*`; keep call sites to a few lines |
| B-05 | `deptry` / `vulture` fail on new extras | Lazy-imported packages look unused | `poe deps`; `poe dead-code` | Add to `DEP002` ignore list and `tests/whitelist.py` with a comment, as other extras do |
| B-06 | `licenses.lock.json` drift in CI | Dependency changed without regeneration | `poe license-lock` | Regenerate and commit |
| B-07 | Windows CI import crash in spaCy or unstructured | Known C-stack issue; `python-magic-bin` loader race | See `pyproject.toml` comments (cymem/murmurhash/preshed caps) | Do not add deps to base; keep extras lazy; re-run the Windows leg with the pinned set |
| B-08 | ranx or numba fails to install on a platform | Numba wheel gap | `pip install ranx` in a clean venv | Keep ranx in the optional extra only; Phase 4 report degrades to "statistics unavailable" with the reason |
| B-09 | `validate=off` changed the envelope | Extra key leaked | Run the byte-identical snapshot test | Only add keys when `validate != off`; declare them in `ALLOWED_EXTRA` |
| B-10 | Retrieval numbers all 0.0 | Degraded (zero-vector) embeddings | Check `degraded` in the ingest result; `corpus-kb doctor` | Start Ollama; pull the model; re-run; the code should have refused to score (see E5) |
| B-11 | Recall differs between chunkers for the same labels | Labels are chunk-ID based | Look at the golden format | Use span or unit labels (Phase 3) |
| B-12 | Offsets do not match text | Segmenter normalized whitespace | Assert `raw[start:end] == chunk.text` | Use an offset-native segmenter or refuse it (E8) |
| B-13 | Self-retrieval hit@1 is low everywhere | Chunks too large, contextual blurbs dominate, or wrong embedding model | Check chunk length distribution and `embedding.model` | Reduce size; test with blurb `apply_to` set to lexical only; re-embed (`ingest_reembed`) |
| B-14 | Calibration hangs or hits MCP time limit | Too many documents | Check `max_docs` and sample size | Lower the cap; run from the CLI; log progress through spans |
| B-15 | DuckDB "database is locked" | Another process holds the file | List processes using the path | Use a unique temp path per run (E13); close other writers |
| B-16 | `enforce` rejects a run after a harmless YAML edit | `profile_sha256` includes comments/whitespace | Diff the file against the lock | Hash canonical parsed YAML (sorted keys), not raw bytes; re-calibrate after real changes |
| B-17 | MinHash flags unrelated documents | Threshold too low or `num_perm` too small | Print Jaccard estimates for the pair | Raise `minhash_threshold`; raise `num_perm`; keep `seed` fixed |
| B-18 | Research transcripts bypass validation | PATH B adapter not wired (D8) | Look for `stages["validation"]` after a research ingest | Call `validation.run` from the research ingest function (shared code) |
| B-19 | Tests need Ollama in CI | A test hit the live embedder | Search for `requires_ollama` marker | Use fakes or recorded fixtures; keep CI deterministic |
| B-20 | `pytest -m ste` fails on new strings | Sentence too long or complex | Read the failing string | Shorten to 20 words or fewer; use simple words |

Escalation: if a blocker needs a schema change, a new error class, a new dependency in base `dependencies`, or a behavior change in an existing tool, STOP and ask the maintainer. Do not route around a rule; change the rule in `docs/research/harness-zero-drift-program.md` first.

---

## 13. File-by-file change map

| File | Change | Phase |
|---|---|---|
| `src/corpus_kb/validation/__init__.py` | NEW: `run(subject, profile, mode)` | 1 |
| `src/corpus_kb/validation/types.py` | NEW: `Subject`, `Finding`, `ValidationReport` | 1 |
| `src/corpus_kb/validation/checks.py` | NEW: structural, lossless, dedup, transcript metadata | 1 |
| `src/corpus_kb/validation/adapters.py` | NEW: `ChunkPlan` to `Subject`; later units to `Subject` | 1 (+ D8) |
| `src/corpus_kb/tools/ingest_common.py` | EDIT: one call after `validate_plan`; merge report into `stages` | 1 |
| `src/corpus_kb/tools/ingest_tools.py` + surface adapters | EDIT: additive `validate` / `profile` args | 1 |
| `src/corpus_kb/research/dynamic_ingest.py` / research handler | EDIT: shared validation call (PATH B) | 1 (D8) |
| `src/corpus_kb/_setup/` doctor | EDIT: read-only validation checks | 1 |
| `src/corpus_kb/profiles/blocks.py` | NEW: parse `chunking`, `validation`, `research_questions` | 2 |
| `src/corpus_kb/profiles/loader.py` | EDIT: register keys, optional fields, delegate to blocks | 2 |
| `src/corpus_kb/_setup/validate_profiles.py` | EDIT: cross-refs for new keys; lock check | 2, 3 |
| `src/corpus_kb/ingestion/strategies/composed.py` | NEW | 2 |
| `src/corpus_kb/ingestion/strategies/segmenters.py` | NEW | 2 |
| `src/corpus_kb/ingestion/registry.py` | EDIT: register `composed` (2 lines) | 2 |
| `src/corpus_kb/validation/labels.py` | NEW: span/unit label mapping | 3 |
| `src/corpus_kb/validation/retrieval.py` | NEW: self-retrieval + arms via existing code | 3 |
| `src/corpus_kb/validation/calibrate.py` | NEW: sweep + lock writer | 3 |
| `src/corpus_kb/cli_eval.py` | EDIT: accept additive label formats | 3 |
| `src/corpus_kb/validation/report.py` | NEW: stats + exports | 4 |
| `profiles/*.yaml`, `profiles/*.rq.yaml` | NEW: profiles for `focus_group`, `survey`, `news`, `mixed` as needed; keep `starter-interview.yaml` unchanged | 2 |
| `pyproject.toml` | EDIT: `[validate]` (datasketch, ranx), `[chunking]` (semchunk), deptry entries | 1-4 |
| `licenses.lock.json` | REGENERATE with `poe license-lock` | each dep change |
| `docs/VALIDATION.md` | NEW: user guide + this runbook | 1 |
| `docs/INGESTION.md`, `INGESTION-STRATEGIES.md`, `EVALUATION.md`, `ADMIN.md`, `retrieval-benchmarks.md`, `CHANGELOG.md` | EDIT | each phase |
| `AGENTS.md` | EDIT: one line per new gate; correct code-chunker drift if confirmed | 1 |
| `tests/...` | NEW per Section 10 | each phase |

---

## 14. Definition of done
1. One ingest call per document; `validate` and `profile` are optional; document type and profile customization always apply.
2. `validate=off` leaves every existing envelope and stored row unchanged (proved by snapshot tests).
3. Validation covers PATH A and PATH B through one shared function.
4. At least one document type has a calibrated, locked profile produced reproducibly; `enforce` detects drift.
5. Segmenters are selectable by config, pass conformance, and are comparable with span-anchored labels.
6. The publication report regenerates from artifacts, with paired statistics, confidence intervals and a limitations section.
7. No paid or cloud dependency; no non-isolated copyleft; `licenses.lock.json` current.
8. All gates green: `poe verify`, `poe gen`, `poe validate-profiles`, Vale, `pytest -m ste`, diff coverage at least 90%.
9. New gates have `poe` targets, CI jobs, enforcement-map rows and AGENTS.md lines.
10. Net-new source lines are within budget or the overrun is justified in the PR.

---

## 15. Review change log (what v2 changed versus v1 and why)

| # | Change | Reason |
|---|---|---|
| 1 | Found TWO ingestion paths; added PATH B coverage (D8) and gate G0 | v1 assumed everything flows through `run_pipeline`. The headline use case (research interview transcripts) appears to use units, not chunks. |
| 2 | "Document types" tied to the five `DATA_TYPES` in the profile loader | v1 listed file formats (slides, web). Profiles key on `data_type`; file formats map through `ingestion.strategies`. |
| 3 | Profile extension moved to a new `profiles/blocks.py`; strict-key behavior documented | `loader.py` rejects unknown keys and is already large (250-line rule). |
| 4 | Lock moved to a separate `<name>.lock.json` | The authored YAML must not be rewritten; the `*.yaml` glob ignores the lock. |
| 5 | Report stored in `ingest_runs.stages` (JSONB confirmed); `finish_run` signature untouched | At least five tests fake that signature. |
| 6 | Added per-call `profile` argument (D5) | `run_pipeline` calls `load_profile(config)` with no override, so "always goes through doc type" needs a per-call choice. |
| 7 | Calibration made a mode of the same call (`validate=calibrate`) | Keeps "one call"; avoids a second tool. |
| 8 | Dropped Pandera (demoted), ir-measures (dropped), Presidio and Chonkie (deferred) | Surgical rule: add a dependency only where it replaces non-trivial code. Leaves datasketch, semchunk, ranx. |
| 9 | Validation placed before the advisory lock and `begin_run` | Read-only; no partial rows; lock not held during slow checks. |
| 10 | Errors reuse the existing taxonomy; no `ERROR_MAP` change | CLI exit codes are scripted by users. |
| 11 | Added E1-E15 prevention table, B-01..B-20 runbook, `docs/VALIDATION.md` | Requested: fast unblocking. |
| 12 | Added honesty rules: refuse to score on degraded embeddings; `insufficient_data` without labels; record tokenizer basis | Matches repo conventions and protects publication claims. |
| 13 | Re-estimated LOC per phase; flagged budget overrun risk and a cut order | v1's 600-line total was not tied to the 1% target. |
| 14 | Phased into independently shippable slices (1 to 4) with exit criteria and rollback | Reversibility and review size. |

## 16. Sources
- Repo files read at `dcf6f61`: `AGENTS.md`, `pyproject.toml`, `src/corpus_kb/tools/ingest_common.py`, `tools/ingest_tools.py`, `ingestion/base.py`, `ingestion/registry.py`, `ingestion/strategies/default.py`, `profiles/loader.py`, `_setup/validate_profiles.py`, `errors.py`, `research/dynamic_ingest.py`, `research/retrieval_eval.py`, `docs/INGESTION-STRATEGIES.md`, `docs/EVALUATION.md`, `profiles/starter-interview.yaml`, migration `022_ingest_integrity.sql` (search excerpt).
- Package pages (fetched 2026-10-25): PyPI pages for datasketch, semchunk, ranx, chonkie, pandera, ir-measures, presidio-analyzer, ragas, great-expectations, mteb, krippendorff.
- Research: ACM "Beyond Chunk-Then-Embed" (doi 10.1145/3805712.3808575); arXiv 2504.19754 "Reconstructing Context"; 2026 chunking benchmark roundups; a Turkish RAG chunking and embedding comparison (Semantic Scholar, 2026-08).
