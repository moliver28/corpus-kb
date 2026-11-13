# GLiNER2 Promotion Harness — DRAFT run (issue #86, ruling 6a)

**Status: pipeline-proof, NOT a promotion verdict.** The draft gold set is
the candidate system's own proposals (owner review pending), so the
candidate's F1=1.0 is circular by construction. The gate verdict below is
the honest output of that biased input, recorded so the post-review rerun
has a baseline to beat.

Environment: Windows, Python 3.11.5, shared venv, CPU-only torch
2.15.0+cpu, gliner2 2.1.0, gliner 0.2.29. Real models: revision `main`
of `fastino/gliner2-large-v1` and `knowledgator/gliner-relex-large-v1.0`
(issue #86 item 4 — pin an exact HF hash — is still open).

## Sample (real corpus, read-only)

- DB: native Postgres 5432, database `corpus_kb` (compose 5433 is dead).
- 40 of 43 docs with live chunk text, stratified: all 6 `text` + 34 of 37
  `markdown`, spread over length terciles; eval window = first 2400 chars.
- 16 transcript + 1 code doc out of scope this round (dialogue/code prose,
  six mention types barely fire).
- Draft gold: 912 spans. The gold text NEVER enters the repo — the review
  package lives in the untracked `.zcode/gold-set-review/` (owner sheet:
  `candidates.csv`, context view `windows.html`).

## Commands (reproducible; worktree needs PYTHONPATH=<worktree>/src)

```
python scripts/run_ner_promotion.py extract --system candidate \
  --gold <local>/gold_draft.jsonl --predictions <local>/predictions_candidate.jsonl
python scripts/run_ner_promotion.py extract --system baseline \
  --gold <local>/gold_draft.jsonl --predictions <local>/predictions_baseline.jsonl
python scripts/run_ner_promotion.py score --gold <local>/gold_draft.jsonl \
  --candidate-predictions <local>/predictions_candidate.jsonl \
  --baseline-predictions <local>/predictions_baseline.jsonl \
  --out <local>/promotion_report_draft.json --seed 42 --resamples 10000
```

Stages are separate processes: two resident GLiNER-class models
segfaulted Windows under memory pressure in the first (single-process)
attempt. Runtimes: candidate 214.5s, baseline 315.3s (16-core CPU).

## Draft metrics (seed 42, 10000 resamples)

| system | P | R | F1 |
|---|---|---|---|
| candidate (`gliner`) | 1.000 | 1.000 | 1.0000 |
| baseline (`ml` zero_shot) | 0.993 | 1.000 | 0.9967 |

Bootstrap delta 0.0033, CI95 [0.0000, 0.0064]. Gate: **FAIL relative**
(0.9967 + 0.02 > 1.0000) → DO NOT PROMOTE. Full numbers in the sibling
JSON. Baseline spans = 919: the same GLiNER2 member plus 7 relex
pseudo-entity spans, which are the only difference between the systems.

## Latent bugs found while running (each fixed on this branch)

1. The gliner backend could not work against real gliner2 2.1.0: span
   flags are required for offsets, the confidence key changed, and labels
   live on the bucket key (`fix: Read real gliner2 2.1.0 span payloads...`).
2. The relex backend called a `predict_relations` API that does not exist
   in gliner 0.2.29; the default `ml` chain silently degraded to regex on
   any machine with a current gliner (`fix: Bridge relex calls...`).
3. Sync extraction ran on the event loop and stalled concurrent requests
   (`fix: Offload blocking extraction passes...`, issue #86 item 3 / L8).

## Recommendation

**Keep `extraction.backend: ml`; do not promote `gliner` on this run.**
Even with owner-reviewed labels the span gate alone cannot see the real
trade-off: `gliner` drops relation extraction entirely (GLiNER2 emits no
relations). The promotion decision needs (a) the reviewed gold rerun and
(b) an explicit owner ruling that relations are dispensable.

## What the owner owes

1. Review `.zcode/gold-set-review/candidates.csv` (confirm / reject /
   correct per row; add missed mentions) — the sheet's README explains.
2. Save the reviewed sheet as `gold_reviewed.csv` in the same directory.
3. Rule on whether relation extraction matters (see Recommendation).
4. Item 4 of issue #86 stays open: pin an exact HF revision hash before
   any promotion lands.
