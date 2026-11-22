# Corpus minimum spec — readiness metric (design note)

Date: 2026-11-17. Status: PROPOSED — owner gate. No implementation rode with
this note. The owner answers the open questions; then an implementation brief
can follow (it gates the S4 journey-time harness).

## The question this metric answers

A net-new user ingests a few documents and asks: "Can I trust answers now, or
should I ingest more first?" Today nothing measures that. The doctor journey
counts build artifacts (`src/corpus_kb/_setup/doctor_journey_state.py:33-42`)
but never scores whether ask works on this corpus. The persona meter pins a
"healthy" example state of 120 chunks and 120 vectors
(`scripts/persona_meter.py:49-51`) but that number is a walk fixture, not a
measured readiness bar.

Readiness here means ONE narrow thing: the ask pipeline can retrieve this
corpus's own content and ground an answer in it. It does not mean the corpus
covers every future question. The wording below keeps that honest.

## Metric (decision 1)

**Primary metric: probe groundedness rate.** The check derives N=4 probe
questions from the corpus itself (one per document), runs each through the ask
steps, and scores every answer the way `verify_answer` already does: claims
entailed by retrieved evidence / total claims
(`src/corpus_kb/ask/steps.py:101-106`). The corpus is READY when the mean
probe groundedness is at or above **0.7** and every probe scored.

Why this metric wins:

- It measures the actual journey leg (ingest -> ask), not a proxy. Chunk and
  document counts say nothing about whether retrieval and grounding work.
- It needs no golden labels, so a net-new user needs nothing extra. The
  existing eval harness scores recall/MRR/nDCG against labeled questions
  (`src/corpus_kb/eval/scoring.py:100-160`); authoring 30+ labels is a
  power-user workflow (`docs/VALIDATION.md:39`) and cannot sit on the
  critical path of a first answer.
- It reuses built code end to end (see the reuse table). Nothing new is
  invented except the probe-question derivation.
- External practice matches the shape: groundedness = share of answer claims
  supported by retrieved context, with 0.70-0.85 as common production bands.

**Guard band (cheap counts, runs first).** Before any probe runs, three
counts decide: non-degraded vectors exist (`> 0`), chunks >= 20, distinct
documents >= 2. A guard-band fail skips the probes and names the thin thing.
These counts are near-free: the journey state reader already counts chunks
and non-degraded vectors
(`src/corpus_kb/_setup/doctor_journey_state.py:65-71`); only the distinct
documents count is new (one `SELECT count(*) FROM documents`, the reader
today counts only dated ones, `doctor_journey_state.py:71-76`). The initial
numbers (20 chunks, 2 docs) are proposals to calibrate on the demo corpus
before landing (open question 2). The persona meter's 120-chunk healthy
state gives an upper sanity reference, not the bar.

Rejected candidates, with reasons:

- Min distinct documents alone: one large doc can be ready; fifty stubs may
  not be. Kept only as a guard band.
- Min chunks alone: chunk counts track strategy and doc size, not answer
  quality. Kept only as a guard band.
- Entity/topic coverage: topics, labels, and predictions are downstream
  analysis products (`BUILD_PRODUCTS` in
  `src/corpus_kb/_setup/doctor_journey_maps.py:41-45`). Ask does not need
  them. They measure the analytics journey, not the ask journey.
- Golden-bench retrieval scores: need labels a new user does not have; also
  measures ranking, not grounded answers.

## Measurement (decision 2)

One read-only readiness probe, computed without hosted LLM calls:

1. **Guard band.** Read the journey state (both backends share
   `read_build_state`, `src/corpus_kb/_setup/doctor_journey_state.py:134-144`).
   Fail -> verdict "not ready" with the thin thing named; skip to 4.
2. **Derive probe questions.** For up to 4 distinct documents (stable order:
   doc id), take the deepest heading path of the document's first chunk;
   form "What does the corpus say about <last heading segment>?". Documents
   without headings use the first eight words of their first chunk. The rule
   is deterministic, needs no model call, and samples the corpus rather than
   the user's patience.
3. **Score each probe.** Reuse the ask steps: retrieve evidence
   (`src/corpus_kb/ask/steps.py:44-64`), generate locally, then decompose and
   entail with the local judge (`src/corpus_kb/rag/judge.py:28-34`,
   default model qwen3:4b on the user's own Ollama; the server already wires
   it behind `judge.enabled`, `src/corpus_kb/server_wiring.py:135`). A probe
   with zero non-degraded retrieval hits scores 0 and names its topic as
   "thin" evidence for the message.
4. **Honest refusals, never fake numbers.** Judge or generator unavailable ->
   the verdict is "unknown", mirroring the calibrate sweep's degraded
   refusal (`src/corpus_kb/eval/scoring.py:110-120`) and the ask abstain
   contract (`src/corpus_kb/ask/steps.py:67-69`). Fewer than 3 scorable
   probes -> "unknown". This vocabulary matches the calibrate outcomes the
   docs already teach (`docs/VALIDATION.md:120-137`).
5. **Threshold provenance.** 0.7 starts as the industry review/block band
   floor (Openlayer flags review below 0.80 and blocks below 0.70). The
   threshold-selection literature warns against arbitrary cutoffs; before
   landing, one offline calibration run on the demo corpus + golden bench
   picks the final number (open question 3).

Verdict set: `ready`, `not_ready` (reason named), `unknown` (reason named).
Asking is never gated by any verdict.

## Surfacing — doctor + UI (decision 3)

- **Doctor:** one new check `corpus_readiness` in the journey section that
  `build_journey_checks` returns
  (`src/corpus_kb/_setup/doctor_journey.py:166-190`). Status mapping:
  ready -> `ok`, not_ready -> `info` with the fix text, unknown ->
  `skipped`. The CLI already prints journey checks with name, status,
  detail, fix, doc (`doctor_journey.py:241-250`).
- **UI:** the SPA reads the same checks from `GET /api/doctor`, which runs
  the CLI's own probe functions so the two surfaces cannot disagree
  (`src/corpus_kb/tools/doctor_tools.py:2-12,47`); the SPA flattens
  `research_checks` in server order (`ui_src/src/pages/setup/doctor.ts:46,106-108`).
  No new envelope keys — the check rides the existing journey-section shape
  (zero-drift rule 3). New UI strings go in
  `ui_src/src/strings/en.json`.
- **Recompute policy:** the probe costs minutes of local compute; doctor
  runs often. Recompute only when the corpus changed since the last verdict
  (digest = document count + chunk count + latest ingest marker), and cache
  one row. Proposed home: a small `corpus_readiness` table via an idempotent
  migration in both migration sets (schema-parity gate applies). Alternative:
  compute only on demand behind an explicit "check readiness" action — no
  table. Owner picks (open question 4).
- This check is doctor/UI text, not a new pipeline; rule 1 (one operation,
  one handler) holds because the probe reuses the ask steps, and any
  on-demand surface wraps the same read-only function.

## Below-threshold behavior (decision 4)

Ready is a label, never a gate. The ask surface has no readiness check today
and gains none. Below threshold, doctor and the UI System-ready strip say,
in plain English: what is thin (vectors, chunks, documents, or the named
topic with zero retrieval hits), the exact ingest command to run (the
journey check's `fix` field pattern, `doctor_journey.py:157-163`), and that
asking is still allowed. Sample detail text: "Your corpus may be thin for
answers about <topic>. You can still ask. Ingest more about <topic> to
improve answers. Run: corpus-kb research ingest." This follows the standard
non-blocking onboarding pattern: instructional empty state, one next action,
no hard wall; the repo already uses the same shape for its empty-corpus
pointer (`doctor_journey.py:170-185`) and its width-abstain precedent
(`src/corpus_kb/dimensions.py:23-27` abstains research, never blocks the rest).

## Costs and risks

- **Compute per recompute:** 4 probes x (1 embed + 1 retrieval query + 1
  local generation + 1 decompose + roughly 3-6 entail calls) = roughly
  20-40 local Ollama calls. Minutes on CPU, well under a minute on GPU.
  Zero hosted tokens, which satisfies the token-cut directive. The digest
  cache makes this once per ingest session, not once per doctor run.
- **False-ready risk (the real one):** probes are derived from the corpus,
  so they prove the pipeline works on its own content, not that the corpus
  answers the user's real questions. A narrow corpus can score 0.8 and still
  miss the user's topic. Mitigations: probes span distinct documents; the
  verdict text says "ready to try", never "complete"; per-probe zero-hit
  topics are surfaced as thin areas. Residual risk accepted and disclosed.
- **False-block risk (bounded):** a down judge yields "unknown", not
  "not ready", so the user is never told to ingest more because local
  compute was off. The guard band can only reword a suggestion; it cannot
  deny an ask.
- **Cost of getting it wrong later:** the metric is a doctor label plus a
  cache row, not schema-heavy state; changing the formula later means a
  recompute, not a migration.
- **S4 contract:** the journey-time harness measures ingest -> ready against
  this verdict. The verdict must be snapshot-able: derivation is
  deterministic, and the harness records the verdict rather than asserting
  bit-equality across runs (local model outputs can drift run to run;
  ordering stays stable per rule 9).

## Open questions (owner: YES/NO each)

1. Approve the primary metric: mean probe groundedness over N=4
   corpus-derived questions, ready at >= 0.7?
2. Approve the guard band shape and initial numbers: vectors > 0,
   chunks >= 20, documents >= 2 (numbers calibrated before landing)?
3. Require the offline threshold-calibration study on the demo corpus +
   golden bench before landing, or accept 0.7 as the shipped default?
4. Recompute policy: digest-cached verdict in a new small table, or
   compute-on-demand behind an explicit action (no table)?
5. Probe source: heading-path derivation as specified, or ship a small
   hand-authored probe pack alongside it?
6. Confirm this metric freezes as the S4 "ingest -> ready" leg's target,
   so the harness and the doctor check cannot drift apart.

## Reuse inventory (nothing new invented except the probe derivation)

| Reused | Where |
|---|---|
| Journey state counts (chunks, vectors) | `src/corpus_kb/_setup/doctor_journey_state.py:33-42,65-71` |
| Journey check shape + empty-corpus pointer pattern | `src/corpus_kb/_setup/doctor_journey.py:166-190,170-185` |
| Claim decompose + entail, groundedness ratio | `src/corpus_kb/ask/steps.py:72-106`, `src/corpus_kb/rag/judge.py:28-34,62-87` |
| Local judge wiring behind `judge.enabled` | `src/corpus_kb/server_wiring.py:135` |
| Honest-refusal vocabulary (calibrate) | `src/corpus_kb/eval/scoring.py:110-120`, `docs/VALIDATION.md:120-137` |
| Doctor snapshot -> SPA, one probe set both surfaces | `src/corpus_kb/tools/doctor_tools.py:47`, `ui_src/src/pages/setup/doctor.ts:106-108` |
| Healthy-state convention (sanity reference) | `scripts/persona_meter.py:49-51` |
| Degraded-vector exclusion semantics | `docs/INGESTION.md:144-158` |
| Width-abstain precedent (gating one feature, not the product) | `src/corpus_kb/dimensions.py:23-27` |

## Web grounding

- Groundedness definition and 0.70/0.80 production bands:
  [Openlayer — groundedness vs faithfulness](https://www.openlayer.com/blog/rag-pipeline-evaluation-groundedness-faithfulness)
- Claim-extraction + verification judge pattern:
  [Langfuse — RAG faithfulness evaluation](https://langfuse.com/resources/engineering/rag-faithfulness-evaluation),
  [DeepEval — faithfulness metric](https://deepeval.com/docs/metrics-faithfulness)
- Threshold selection warning (validate, do not guess):
  [arXiv 2412.12148 — how to choose a threshold for an evaluation metric](https://arxiv.org/html/2412.12148v1)
- Non-blocking onboarding/empty-state patterns:
  [Appcues — onboarding UX patterns](https://www.appcues.com/blog/user-onboarding-ui-ux-patterns),
  [Emergen — onboarding best practices](https://emergen.io/blog/ux-best-practices-for-onboarding-flows-designing-seamless-first-time-experiences-in-2025)
