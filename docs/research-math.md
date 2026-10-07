# Research Math (v5 §8/§12 — deductive scoring, calibration, conformal)

Every formula below states its v5 source and its calibration source so
maintainers never re-derive policy from code. This file is extended as later
todos land (todo 17 owns the exhaustiveness/overlap/IRR catalog).

## Layering contract (r11 — who owns what)

- **tau is the decision rule**: per-code `tau_a` / `tau_qa` / `delta`, fitted
  by G2 on raw cosine similarities, are the ONLY quantities that accept or
  reject a code (v5 §8 semantics preserved).
- **temperature/ECE calibrate reported confidence**: softmax(S/T) and its
  expected calibration error are display- and G3-signal-only — they never
  move a tau or flip an accept/reject (Guo et al. 2017).
- **conformal sets are the guarantee layer**: RAPS-style prediction sets give
  distribution-free coverage P(Y ∈ C(X)) ≥ 1 − α and influence ROUTING only —
  they never override a tau decision (Angelopoulos et al. 2021; TACL 2024).
- **gray-zone band, margin, and set-size are routing triggers**: a score in
  `[tau − m_gz, tau)` routes to review; a conformal set-size > 1 inside that
  band relabels the reason `conformal` — both trigger routing, neither
  decides acceptance.

## Three-view scoring (v5 §8)

With `U` the matrix of unit-view embeddings and `P` the prototype matrix
(both L2-normalized at the boundary, zero rows left zero so degraded runs
abstain), scores are the exact cosine matmul `S = U Pᵀ`; per code and view
the unit score is the max over prototypes: `s_a`, `s_qa`, `s_q`.
`U` is streamed in batches of `SCORING_BATCH_ROWS = 1024` (module constant;
r9 demotion from config) so the full unit matrix is never materialized.

Prototypes are k-medoids (PAM: BUILD init + best-improvement SWAP, numpy
only) over gold exemplar vectors — never mean centroids — chosen per code
and per view.

## Decision rules (v5 §8, verbatim semantics)

- **Explicit evidence**: assign when `s_a >= tau_a(code)`
  (`evidence_basis = explicit_in_answer`).
- **Question-dependent evidence**: assign only when ALL hold: `s_qa >= tau_qa`;
  information gain `g = s_qa − s_q >= delta`; stance in {affirm, partial} or
  substantive elaboration; not a denial/deflection; not a question echo
  (`s_q ≈ s_qa`). Confidence capped at medium, routed to review by default
  (`evidence_basis = question_dependent`).
- **Never assign from `s_q` alone.** Deny/deflect answers are never
  question-dependent-assigned.
- **Interpretive codes** (`is_interpretive`) always route to human review.
- **Threshold-unreliable codes** (gold n < 20) always route to review.
- **Multi-label**: every code above its threshold is accepted.
- Coverage is reported split: `Cov_explicit` and `Cov_qdep`
  (`Cov = |{i : any accepted code}| / N` per basis).

## G2 threshold calibration (v5 §12 G2)

Per code: stratified k-fold CV (folds balanced by code presence) over the
gold subset; prototypes are REBUILT inside each training fold so a held-out
exemplar is never scored against a medoid chosen from it; per-fold
thresholds are stored (`cv_range` min/max) and their spread IS the gray-zone
margin: `m_gz = std(fold thresholds, ddof=1)` — calibrated alongside tau,
never hardcoded. Final thresholds are the fold medians. Gold n < 20 flags
the code `threshold_unreliable` (routed to review). Seeds
(`calibration_split_seed`, `temperature_seed`) are pinned; refits are
byte-stable for identical seeds.

## Calibration upgrades (r10)

- **Temperature scaling**: one parameter T fitted on the held-out
  calibration split by minimizing multi-label cross-entropy of
  `softmax(S/T)`; ECE reported at 10 equal-mass bins (Guo et al. 2017).
- **Conformal prediction sets**: multi-label APS/RAPS-family sets from the
  held-out calibration split; the quantile is
  `ceil((n_cal + 1)(1 − α))/n_cal` with empirical coverage asserted ≥ the
  nominal `1 − α` on the calibration sample (Angelopoulos et al. 2021;
  TACL 2024 survey arXiv:2405.01976). Units whose set-size > 1 inside the
  gray zone route to review with reason `conformal`.
- The live run derives its calibration split leave-one-out over gold
  exemplars (same rebuild discipline as the G2 folds).

## References

- Guo, Pleiss, Sun, Weinberger (2017), *On Calibration of Modern Neural
  Networks* — temperature scaling, ECE.
- Angelopoulos, Bates et al. (2021), *A Gentle Introduction to Conformal
  Prediction*; Angelopoulos et al. (TACL 2024), arXiv:2405.01976 —
  multi-label conformal sets.
- v5 §8 (deductive rules), §12 (G2 gate).
