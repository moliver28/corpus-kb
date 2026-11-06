# Analysis question-routing program (design)

Date opened: 2026-11-03. Status: DRAFT for owner review. No code written yet.

## Goal

Map a plain-language user question to the right analysis method, now and as methods grow. Expert NLP analyst conditions drive the choice. Novices see guided, plain-English rationale for each choice. Experts can override. The desk research (cosine family, space health, divergence, optimal transport, graph similarity, entity match-or-create) seeds the method catalogue, but the routing axes must generalize to any future method.

Chosen architecture (owner may override): cascade router — deterministic fast path first, an ambiguity ladder second, an optional LLM fallback last. Evidence: production systems ship rules-first (JAIR 2026 routing survey, arXiv 2502.00409); the embedding fast path plus LLM fallback is the semantic-router pattern; cascade quality collapses when router error passes about 0.2 (RouterBench, arXiv 2403.12031), so every stage is calibrated and benchmarked, not trusted.

## What exists and is reused

- Method register: `config/analysis_methods.yaml` (30 methods) with `MethodRecord` (37 axes) in `src/corpus_kb/analysis/method_register.py`; deterministic rubric `rank_methods` in `method_register_rubric.py`; op `analysis_select_method` in `src/corpus_kb/tools/method_select_tools.py` on all four surfaces.
- Intent-classifier precedent: `src/corpus_kb/handlers/router_handler.py` (cosine to prototype centroids, confidence threshold, honest fallback).
- Guided UI: `ui_src/src/pages/analyze/AdvisorCard.tsx` already renders rank, why, validation, permitted claims, and hands off via `onRunMethod(opId)`.
- Calibrate machinery: `src/corpus_kb/eval/calibrate.py` (Holm-corrected paired tests) and recipe overrides in `eval/recipes.py`.
- Known gap this program closes: `task_description` is presence-checked only (`method_select_tools.py:50-51`). No free-text question understanding exists anywhere in the repo.

## Registry enrichment (wave 1)

Extend `MethodRecord` with fields grounded in how the literature states conditions and failure modes. All fields optional at first; the validator enforces them once populated.

| Field | Content | Source pattern |
|---|---|---|
| `task_type` | one of representation, discovery, measurement, prediction, causal_inference | Grimmer/Roberts/Stewart five tasks |
| `comparison_structure` | none, subset_vs_subset, paired, temporal, cross_corpus, model_vs_model | stats decision tables (UCLA OARC style) |
| `output_form` | point_answer, ranked_list, statistic, cluster_map, graph, narrative | Tableau/Grimmer output forms |
| `evidence_basis` | vector, lexical, claims, graph, labels | repo seam inventory |
| `statistical_rigor` | descriptive, inferential_null, inferential_calibrated | calibration literature |
| `cost_tier` | interactive, batch | cascade economics |
| `question_phrases` | 5-10 seed reference utterances per method; embedding centroid material | semantic-router reference sets |
| `assumptions` | statistical conditions plus what breaks when violated | metric-family conditions |
| `null_calibration` | null model, sample size, correction family | permutation-null guardrails |
| `failure_modes` | observed failures, cited | metric-family caveats |
| `scope_limits` | when manual close reading wins | Baden et al. 2022 |
| `cost_scale` | complexity class plus practical ceiling | metric families |
| `suggests` | follow-up question templates | Snowy/Foresight next-question pattern |
| `evidence_citation` | source URL, tier, year | evidence field |

`scripts/gen_method_register_docs.py` extends to render the new columns; `poe gen` stays the drift gate. The core vocabularies live beside the models in `method_register.py`; the six routing vocabularies and the routing checks live in `method_register_routing.py`.

## Question intent schema (wave 2)

New `src/corpus_kb/analysis/question_intent.py`: dataclass `QuestionIntent` with the axes above plus target references (documents, entities, time window), confidence, outcome (`resolved | ambiguous | insufficient`), and the method ranking. It is a typed, storable artifact: every routed answer keeps its intent next to the result, so choices stay auditable (structured-output research: schema-valid is not meaning-correct; storing the parsed intent is the audit trail).

## Router (wave 2)

New `src/corpus_kb/analysis/question_router.py`.

1. Fast path (deterministic): phrase rules over `question_phrases`, then embedding cosine to per-method centroids using the existing `OllamaEmbedder` (SHA256 cache, zero-vector fallback). Decide by threshold plus score margin; both configurable (`analysis.routing.confidence_threshold`, `analysis.routing.margin`) and calibrated against corpus-derived permutation nulls, never copied constants.
2. Ambiguity: below margin, emit `ambiguous` with the top three readings (method plus a plain-English restatement of the question under that reading). The user picks. This is the CRAG Correct/Incorrect/Ambiguous outcome plus the DataTone ambiguity-widget pattern, and it matches the two-stage SELECT-THEN-ROUTE result (EMNLP 2025): pick the branch, then rank within it.
3. LLM fallback (wave 5, config-gated, default off): the ask-op LLM receives a register summary and returns a structured intent; a schema check plus a sanity check on the method id run before anything executes. Each fallback `{question, outcome}` is appended to a labeled-examples file that grows the centroid reference sets (semantic-router learning loop).

Degraded mode (no Ollama): phrase rules only, envelope records the degraded mode, matching the honest-envelope convention.

The resolved intent feeds the existing `rank_methods` rubric as constraints. Routing ranks, the rubric orders within a branch. Determinism: fixed tiebreakers (rubric already sorts by id last).

## Operation and surfaces (wave 3)

New op `analysis_route` (handler in `src/corpus_kb/tools/`, thin adapters everywhere, per the one-handler rule):

- Input: `question` (free text), optional expert overrides (method id, axes).
- Envelope (amended in wave 3, post W2 review): `{status: resolved|ambiguous|degraded, intent, ranked: [{method_id, name, why, run_hint}], interpretations?, trace}`. The envelope `status` carries exactly three values: `resolved`, `ambiguous`, `degraded`. The router outcome `insufficient` never reaches the envelope: the op layer raises `no_method_matches` for it. `degraded` is flagged by the envelope status AND visible as `intent.note == "degraded"`. The ambiguous ladder in degraded mode is keyed off `intent.status == "ambiguous"`, not the envelope status: `interpretations` are present exactly when the intent is ambiguous, degraded or not, and the op layer caps them at three. `run_hint` carries the op name plus params so the UI `onRunMethod` bridge starts the real analysis; `run_hint.op` may be null, which means the method runs with people outside the app, and the UI renders a human-led note for it. `ranked` rows come from the rubric join (cap five); an expert override puts the chosen method first and sets its `why` to "Set by the analyst." A decided intent also adds `suggestions`: up to three verbatim follow-up questions from the chosen row's `suggests` metadata. Presence rides the intent: `status == "resolved"` and a chosen method id. Degraded answers keep their chips. Ambiguous answers have none.
- Catalogue row, parity leg, surface registry row, `poe gen`, error rows in `ERROR_MAP` (`question_too_short`, `no_method_matches`) — the standard zero-drift checklist.

`analysis_select_method` stays unchanged for expert direct use.

## Explanation layer (waves 3-4)

Progressive disclosure, per IUI 2019 and TiIS 2020 evidence that layering beats full dumps:

- Always visible: one-line plain-English why (STE100, 20 words or fewer), templated from method metadata.
- One click: full trace — axis values, stage scores, margin, rejected methods and their scores.
- Always available: expert override (pick any method; the trace records the override).
- After an answer: up to three suggested next questions from `suggests` metadata.

UI lands in `AdvisorCard` as a question mode next to the current constraint form. Strings in `ui_src/src/strings/en.json`, gated by `pytest -m ste`.

## Evaluation and calibration (waves 2 and 5)

- Golden bench: `tests/fixtures/routing_golden.jsonl` (question, expected method ids, expected axes). Report accuracy and regret (rubric rank distance between chosen and expected). The accuracy floor enters `scripts/quality-baselines.json` from the first honest baseline run, then only moves up.
- Calibrate extension: routing candidates (threshold, margin, embedder choice, later LLM fallback on/off) join the existing Holm-corrected sweep family, so router tuning goes through the same evidence gate as retrieval recipes. Confirmatory winner via Holm, exploratory screening via BH-FDR, effect size reported alongside.

## Testing

Unit tests with a fake embedder; degraded-mode tests without Ollama; parity legs on all four surfaces; golden-bench gate; ste string gate; deterministic ordering tests. Full local gate suite (`poe lint`, `poe types`, `poe verify`, `poe gen`) before any push.

## Waves

| Wave | Branch | Type | Done when |
|---|---|---|---|
| W1 | `feature/analysis-routing-w1` | registry | New fields plus vocabularies validated; generator and docs regenerated; all gates green; no behavior change |
| W2 | `feature/analysis-routing-w2` | feat | Intent schema; fast path plus ambiguity outcome; golden bench with accuracy floor recorded; deterministic tests green |
| W3 | `feature/analysis-routing-w3` | feat | `analysis_route` on four surfaces; parity plus registry plus gen green; AdvisorCard question mode with one-line why, trace, override; ui gates green |
| W4 | `feature/analysis-routing-w4` | feat | Interpretation picker for ambiguous questions; suggested next questions |
| W5 | `feature/analysis-routing-w5` | feat | LLM fallback behind config flag; fallback example log; calibrate routing sweep; regret metric in baselines |
| W6+ | separate issues | feat | Method expansion packs (drift tests, keyness, Burrows Delta, JSD/OT set comparison, Vendi, PyOD anomaly, agreement aggregation, RankMe health) — YAML rows plus handlers; the architecture accepts them without change |

## Key evidence

- Adaptive-RAG (NAACL 2024): outcome-labeled complexity tiers; cheap-sufficient routing wins.
- semantic-router pattern: embedding fast path, threshold plus margin, LLM fallback, fallbacks become examples.
- RouterBench (ICML 2024): judge error above ~0.2 collapses cascades; calibration is the binding constraint.
- SELECT-THEN-ROUTE (EMNLP 2025): two-stage taxonomy routing beats flat classification.
- JAIR 2026 survey: production runs rules and cascades, not learned routers.
- Grimmer/Roberts/Stewart 2022: five-task method taxonomy (representation, discovery, measurement, prediction, causal inference).
- DataTone (UIST 2015); Tableau Pulse: ranked interpretations; corrections become constraints.
- IUI 2019; TiIS 2020; NN/g: progressive disclosure; rationed transparency builds trust.
- Metric families and calibration guardrails: full table in `.zcode/analysis-routing-research-2026-11-03.md` (workspace copy; to be folded into docs as W6 packs land).

## Open items

None blocking W1. Owner may still override the cascade choice (deterministic-only is the fallback if LLM fallback is unwanted).
