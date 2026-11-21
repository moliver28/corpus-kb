# UXR Registry-Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the corpus-rag plugin apply the engine's 12 method ops + router to novice UXR tasks, add 3 new UXR engine methods, and derive hooks from live route data — on both backends, no plugin bloat.

**Architecture:** Registry stays the spine; plugin becomes a registry client through the existing op_runner path (no new runner). Hooks gain one mark-gated, fail-open shell-out to `corpus-kb analysis-route`. Three new engine methods land registry-first through the full surface checklist.

**Tech Stack:** Python 3.11 stdlib (plugin), asyncpg/duckdb engine handlers, pytest, poe gates.

**Spec:** docs/superpowers/specs/2026-11-20-uxr-registry-spine-design.md (D1-D8 + waves W1-W4; this plan implements W1-W3; W4 = orchestration-level review, not tasks here).

## Global Constraints

- Branches: `feature/uxr-plugin-wiring` (Tasks 1-6) and `feature/uxr-engine-methods` (Tasks 7-9), both off master @ 5c80450; conventional single-line commits (`feat:`/`test:`/`chore:`).
- Plugin: stdlib only, zero new deps; new code in NEW small modules; never grow `codify_commands.py` (321 lines).
- Engine: register rows in BOTH `config/analysis_methods.yaml` and `src/corpus_kb/config/analysis_methods.yaml`, byte-identical; import direction `api → tools → handlers → storage`; no `type: ignore`; `from __future__ import annotations`; 250-line soft limit; ASD-STE100 plain English for all new user-facing strings.
- Error taxonomy for new ops (typed errors, one failure class = one code); auto-approve classification in `surface_registry.py` (`SERVER_AUTO_APPROVE`/`AUTO_APPROVE_WRITE_TOOLS`) locked by `tests/test_auto_approve_lockstep.py`.
- Zero regression: no new warnings; `poe gen --check` clean after any register change.
- Hook lane: marks checked BEFORE any CLI call; 10 s timeout; fail-open to static text; plain stdout, exit 0 always.

## Review Focus

1. **Degraded route status** — `status: "degraded"` (string, no boolean): plugin text must say routing ran degraded, never claim a confident winner. Test: Task 4 asserts fallback/degraded rendering.
2. **DuckDB `/api/query/sql` dialect** — plugin posts schema-qualified SQL (`uxr_commands.py:51-63`); must work on duckdb. Test: engine twin test hitting the route via duckdb gateway (Task 9's test leg) + Task 6 gate.
3. **Nudge latency** — a prompt in an unmarked study must not block >10 s even if the engine is down. Test: Task 4 timeout/fallback unit test.
4. **Marker safety in themes.md** — coding_export rows must append between existing markers without corrupting model narrative on re-run (idempotent). Test: Task 5 double-run assertion.
5. **Register mirror drift** — two yaml files must stay byte-identical. Test: existing lockstep test extended to catch row-count mismatch after W3 (Task 7).

---

### Task 1: corpus-analyze route-then-run (plugin)

**Files:**
- Modify: `plugins/corpus-rag/skills/corpus-analyze/SKILL.md` (step 2 area), `plugins/corpus-rag/commands/corpus-analyze.md`
- Test: `plugins/corpus-rag/tests/test_skill_tables.py` (new)

**Interfaces:**
- Produces: prose flow "run `corpus-rag op analysis-route --question <q>` → present `ranked` rows (`method_id`, `name`, `why`, `run_hint`) → on user confirm run `corpus-rag op <run_hint.op>` with `run_hint.params`". Degraded status renders "routing ran in degraded mode" note.

- [ ] **Step 1:** Rewrite skill step 2 + command file to the route-then-run flow above (plain English, ≤20-word sentences). Replace the dead-end "offer to run the method" with the run command template.
- [ ] **Step 2:** Test: new `test_skill_tables.py` asserts SKILL.md contains `analysis-route`, `run_hint`, and no longer contains "offer to run the method". Run: `python -m pytest plugins/corpus-rag/tests/test_skill_tables.py -v`. Expected: PASS (write test first, see it fail, then edit).
- [ ] **Step 3:** Commit `feat: corpus-analyze routes then runs methods`.

### Task 2: corpus-research op table gains method rows (plugin)

**Files:**
- Modify: `plugins/corpus-rag/skills/corpus-research/SKILL.md:18-30` (op table)
- Test: covered by Task 6's engine-side gate.

**Interfaces:**
- Produces: one table row per method op (12) + `analysis-route`, format `corpus-rag op <name> ...` matching existing rows.

- [ ] **Step 1:** Append rows after existing ones (grouped: router first, then methods alphabetical). One line each, plain English.
- [ ] **Step 2:** Commit `feat: corpus-research op table lists analysis methods`.

### Task 3: corpus-uxr method-chain steps (plugin)

**Files:**
- Modify: `plugins/corpus-rag/skills/corpus-uxr/SKILL.md` (after theme-proposal step)
- Test: `plugins/corpus-rag/tests/test_skill_tables.py` (extend)

**Interfaces:**
- Produces: a "method support pass" step: run `keyphrases-extract` on the question set, `clusters-inspect` to group, `sentiment-screen` per question, `contradictions-screen` across stances — each with one plain-English sentence of why, and the rule that method output ADVISES, researcher judgment decides (matches `epistemological-caution.md`).

- [ ] **Step 1:** Insert the step + rationale prose; reference `references/epistemological-caution.md`.
- [ ] **Step 2:** Test: assert SKILL.md names all four ops and "advises". PASS then commit `feat: corpus-uxr chains registry methods into themes`.

### Task 4: analyze-lane route-derived nudge (plugin)

**Files:**
- Create: `plugins/corpus-rag/hook_route_text.py` (derives nudge text from route JSON; pure function, testable)
- Modify: `plugins/corpus-rag/hook_events.py:66-87` (analyze lane: after mark gate passes, run `corpus-kb analysis-route --question <prompt>` via `subprocess.run(..., timeout=10)`, parse stdout, call `hook_route_text`); keep `LANE_TEXT` static fallback on TimeoutExpired/JsonDecodeError/FileNotFoundError/any failure.
- Test: `plugins/corpus-rag/tests/test_hook_route_text.py` (new)

**Interfaces:**
- Consumes: route envelope keys `status`, `ranked[{method_id,name,why,run_hint{op,params}}]` (verbatim from `analysis_route_envelope.py:127-138`).
- Produces: `route_nudge_text(route: dict, question: str) -> str` — first line names top method + why (≤20 words), second line the run command from `run_hint`, degraded status prepends "Routing ran in degraded mode."; empty/malformed route → `""` (caller keeps static text).
- Mark ordering: the daily mark `prompt-<slug>-analyze-<YYYYMMDD>` check happens BEFORE the subprocess (today's order already gates first — keep it).

- [ ] **Step 1:** Write failing tests: happy path (top method + command line), degraded prefix, malformed JSON → "", subprocess timeout path (monkeypatched) keeps static text.
- [ ] **Step 2:** Run: `python -m pytest plugins/corpus-rag/tests/test_hook_route_text.py -v`. Expected FAIL (module missing).
- [ ] **Step 3:** Implement `hook_route_text.py` + wire the lane in `hook_events.py` (subprocess inside try, timeout=10, fail-open).
- [ ] **Step 4:** Run full plugin suite: `python -m pytest plugins/corpus-rag/tests/ -v`. PASS.
- [ ] **Step 5:** Commit `feat: analyze-lane nudge derives from analysis-route`.

### Task 5: data merges (plugin)

**Files:**
- Create: `plugins/corpus-rag/uxr_merge.py` (merge helpers, pure functions)
- Modify: `plugins/corpus-rag/uxr_summaries.py` (themes writer: append coded-evidence section between markers at :32-41), `plugins/corpus-rag/uxr_rollup.py:91,140` (evidence section), `plugins/corpus-rag/uxr_commands.py` (unmatched-class assist calls `search_similar` op via op_client pattern used by study_query), `plugins/corpus-rag/uxr_analysis.py` (Contract step handler: no report tool → run rollup as evidence source with "evidence check ran in degraded mode" note, exit 0)
- Test: `plugins/corpus-rag/tests/test_uxr_merge.py` (new)

**Interfaces:**
- Produces: `merge_coded_evidence(themes_text: str, rows: list[dict]) -> str` (idempotent: replaces its own previous section between its markers, never touches model narrative); `quote_second_opinion(quote: str) -> dict | None` via `search_similar` (advisory flag only — difflib contract unchanged); contract-parity default path.
- The coding_export call site: the uxr themes handler runs `corpus-rag op coding_export` output parsing — reuse `op_runner`/op_client patterns from `codify_commands.py` WITHOUT editing that file.

- [ ] **Step 1:** Failing tests: idempotent double-merge (same output twice), narrative untouched, degraded contract path exit 0, second-opinion returns None when op errors.
- [ ] **Step 2:** Implement helpers + wire the four call sites.
- [ ] **Step 3:** Suite PASS; commit `feat: merge codify evidence into uxr themes and rollup`.

### Task 6: engine-side SKILL-table gate

**Files:**
- Create: `tests/test_plugin_skill_table_gate.py`
- Test IS the deliverable.

**Interfaces:**
- Consumes: contract catalogue names (`corpus_kb.contracts`); plugin SKILL.md table rows (`corpus-rag op <name>`).
- Produces: test asserting every op row in `plugins/corpus-rag/skills/corpus-research/SKILL.md` + `commands/*.md` matches a real catalogue op name (and lists all 12 method ops + analysis-route after Tasks 1-2).

- [ ] **Step 1:** Write test parsing SKILL.md rows → assert each name in the catalogue; assert the 13 expected names present. Run against current master first: the 12 method rows FAIL (they don't exist yet) → this pins Tasks 1-2. After Task 2: PASS.
- [ ] **Step 2:** Commit `test: gate plugin op table against engine catalogue`.

### Task 7: register grows to 33 (engine)

**Files:**
- Modify: `config/analysis_methods.yaml` + `src/corpus_kb/config/analysis_methods.yaml` (rows 31-33), `src/corpus_kb/analysis/method_register.py:111-117` (widen id range — make the bound data-driven: `len(EXPECTED_IDS)` from the loader, keep the validator), `src/corpus_kb/analysis/method_register_loader.py:20,56-64` (EXPECTED_IDS ledger → 01-33), `tests/test_method_register.py:160-163,194` (pins 30 → 33)
- Test: `tests/test_method_register.py` (extend)

**Interfaces:**
- Produces: register rows (fields per existing 38-field schema; every field populated):
  - `31 survey-openend-codes`: op `survey_openend_codes`, task_type `count`, output_family `counts`; consumes survey responses (shape per Task 9 entry check — adjust `consumes`/`produces` fields to the real storage shape, document in row `selection_notes`).
  - `32 usability-severity`: op `usability_severity`, task_type `score`, outputs 0-4 severity per finding with rubric basis in `usage_guidance`.
  - `33 emotion-tag`: op `emotion_tag`, fixed taxonomy (Plutchik 8), `nli` evidence basis like method `nli-classify`.

- [ ] **Step 1:** Entry check FIRST: read `src/corpus_kb/ingestion/strategies/survey.py` + stored chunk/row shape for survey responses; write the real consumes/produces into row 31. If storage shape defeats open-end coding → STOP, report, owner re-scope (spec D8).
- [ ] **Step 2:** Write failing test: `len(register.methods) == 33`, ids 01-33 unique, mirrors byte-identical. Run → FAIL.
- [ ] **Step 3:** Edit validator/ledger/pins + both yaml rows. `poe gen` → clean. Run: `python -m pytest tests/test_method_register.py -v`. PASS.
- [ ] **Step 4:** Commit `feat: register gains survey usability and emotion methods`.

### Task 8: three method surfaces (engine)

**Files (per method, identical pattern):**
- Create: `src/corpus_kb/tools/uxr_method_tools.py` (three handlers, one file if ≤250 lines else split)
- Modify: `src/corpus_kb/cli/cli_analysis_methods.py` (3 wrappers), `src/corpus_kb/api/routes_analysis_methods.py` + `api/http.py:231` (3 routes), `src/corpus_kb/api/_socket_rows.py` (3 `_Spec` arms), `src/corpus_kb/contracts/catalogue_analysis.py` (3 rows, HTTP+MCP+CLI+SOCKET specs), `src/corpus_kb/surface_registry.py` (op rows, help, skill entries, auto-approve sets), `tests/test_surface_parity_deep.py` (3 `_Leg` rows following sentiment-screen at :1523-1526), `src/corpus_kb/telemetry/attribution.py:39`, `src/corpus_kb/_setup/doctor_journey_maps.py:36`
- Test: `tests/test_uxr_method_tools.py` (new), parity rows

**Interfaces:**
- Produces: `async def survey_openend_codes(...)`, `async def usability_severity(...)`, `async def emotion_tag(...)` — signatures copied from `sentiment_screen` (`tools/sentiment_tools.py:128`): same request/response envelope pattern, typed errors. Handlers: survey = cluster answers (embedding + HDBSCAN-free: reuse `clusters_inspect` primitives), label per cluster, count; severity = NLI/lexical rubric match to Nielsen 0-4 anchors; emotion = fixed-taxonomy NLI classify reusing `nli_classify` primitives.
- Names: `survey_openend_codes`, `usability_severity`, `emotion_tag` (verified collision-free).

- [ ] **Step 1:** Parity/catalogue/registry rows FIRST (registry-first rule). Parity test legs fail → then implement.
- [ ] **Step 2:** Handlers + CLI + routes + socket arms. Run parity suite + `poe gen --check`. PASS/clean.
- [ ] **Step 3:** `python -m pytest tests/test_surface_parity_deep.py tests/test_uxr_method_tools.py -v`. PASS. Commit `feat: survey usability emotion method surfaces`.

### Task 9: method tests + twins + persona baseline (engine)

**Files:**
- Modify: `tests/test_uxr_method_tools.py` (duckdb twin legs per PR #525 pattern), `tests/fixtures/persona_meter/report.v1.json` (re-record)
- Test: `tests/test_retrieval_eval_duckdb.py`-style twin legs for the three handlers.

**Interfaces:**
- Consumes: duckdb gateway fixture pattern from `tests/test_research_retrieval_duckdb.py`.
- Produces: each new handler proven on postgres + duckdb twins; persona-meter walk covers 33 methods within ceiling.

- [ ] **Step 1:** Twin tests for the three handlers (degraded zero-vector mode included).
- [ ] **Step 2:** Re-record persona baseline: `python scripts/persona_meter.py` → commit updated `report.v1.json` if only additive; ceiling violation → stop, report.
- [ ] **Step 3:** `python -m pytest tests/test_uxr_method_tools.py -v` PASS. Commit `test: twins and persona baseline for new methods`.
