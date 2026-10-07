# Research Math (v5 §8/§9/§12 — deductive scoring, inductive engine, calibration, conformal)

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

## Inductive engine (v5 §9, todo 15)

- **Atomic observations**: one temp-0 LLM summary per codable unit,
  question-context aware for interviews; the stored row logs
  `prompt_id` + `model` + `temperature` and the `question_dependent` flag
  (v5 §9.1). A malformed response degrades to the raw answer text with
  `parse_fallback` recorded — summaries never crash a run.
- **Clustering**: summaries are embedded through the shared 1024-d embedder;
  UMAP (`n_components=10` for clustering — 2-D is display-only,
  `metric='cosine'`, `random_state=42`, `transform_seed=42`) then HDBSCAN
  (`min_cluster_size = max(5, ceil(0.01·N))`, `min_samples =
  min_cluster_size`, cosine). Determinism pins are code constants
  (`coding/inductive_cluster.py`) and are recorded in every run manifest
  (r7): identical inputs produce identical clusters (asserted by the
  extras-installed fixture test). The optional `umap-learn`/`hdbscan` stack
  is reached ONLY via the typed-Protocol shim
  (`coding/_inductive_types.py`, house pattern) — without the extra the
  engine reports `unavailable` and the deductive path is untouched (r8).
- **Assignment-space contract (r7)**: soft-assignment centroids, drift
  snapshots, and the Tier-0 entropy all live in the ORIGINAL
  summary-embedding space; the UMAP output is HDBSCAN's input only, never
  the assignment space.
- **Soft cluster-assignment entropy** (v5 §9.3, Student-t kernel): over the
  `k = clamp(k, 2, 4)` nearest centroids,
  `q_ij = (1 + d_ij²/a)^(−(a+1)/2) / Σ_j'`, `H_i = −Σ_j q_ij log q_ij`,
  reported normalized `H_i / log k ∈ [0, 1]` (category-discovery lineage).
  `d_ij² = 2(1 − cos)` — the squared euclidean distance between
  unit-normalized vectors (on the unit sphere raw cosine distance compresses
  toward 1 in high dimensions and erases the kernel's contrast);
  `a = 0.02` is the kernel-sharpness pin (both recorded in the run
  manifest). Confident members land well below the gate, between-theme
  units near 1. High `H_i` is the ONLY gate for the LLM "existing code vs
  new code" meta-decision (v5 §9.4), and the entropy rows land in
  `research_signals` (`tier=0`, `soft_cluster_entropy`, `n_clusters`) for
  todo-16's review-priority refit.
- **Cadence** (v5 §9.5): centroids refresh per incremental batch (means over
  members in the original space); a mean cosine drift between consecutive
  snapshots above `centroid_drift_threshold` (config, default 0.15) triggers
  an EARLY full re-cluster; a full re-cluster also fires every
  `recluster_every_batches` batches (config, default 8 — within §9.5's 5-10).
- **DBCV validity** (r10): HDBSCAN `relative_validity_` (Moulavi et al.
  2014 — density-appropriate, unlike silhouette on non-convex clusters) is
  reported per clustering run and tracked against the previous run's
  checkpoint; a >20% drop (`dbcv_drop_threshold`) sets `dbcv_drop_flag` →
  re-cluster review. A missing/non-positive baseline never flags.
- **Noise bucket**: HDBSCAN label −1 units go to `research_noise_queue`
  (manual-review queue, status `pending`) — never discarded. The queue is a
  UNION: reduced-space HDBSCAN noise PLUS original-space density outliers
  (a second HDBSCAN fit over the unit-normalized summaries with the same
  `min_cluster_size`/`min_samples`/cosine pins). Rationale (measured on the
  todo-15 fixture): the UMAP fuzzy graph rescues isolated points on small-N
  qualitative corpora — every injected outlier lands within core distance of
  a theme after reduction, so the reduced fit alone never emits −1; the
  original space is already the assignment space (r7), and the queue is
  review-only, never removing a unit from its cluster.
- **ISR per batch**: Incremental Sampling Rate (`coding/saturation.py`,
  `isr(unique_codes, total_applications)`) computed per incremental batch —
  unique clusters touched / units in the batch — recorded in the CodingRun
  checkpoint payloads.
- **Proposed-code governance** (v5 §9.6): every non-noise final cluster
  lands in `research_proposed_codes` with cluster id, centroid snapshot
  (PROVENANCE ONLY — summary space; never a scoring prototype, r7), member
  unit_ids, proposing batch, and run DBCV. Human promotion happens ONLY via
  the named surface `corpus-kb codebook promote` (CLI) / `codebook_promote`
  (MCP): member unit_ids → A/QA/Q view embeddings → k-medoids
  (`build_view_prototypes`) → `PrototypeUpdated` exemplar refs on a NEW
  `codebook_version` aggregate — prototypes live in the DEDUCTIVE
  unit-embedding space, rebuilt from the new version's gold (r7 CRITICAL).
- **Promote-time duplicate gate**: the re-derived answer-view prototypes'
  max cosine vs every existing code's gold-derived answer prototypes; above
  `tau_dup` the promotion BLOCKS with a merge suggestion (exit 3 / status
  `duplicate_blocked`). `tau_dup` is calibrated from gold when prototype
  geometry exists: `max inter-code prototype cosine + 0.05`, clamped to
  [0.7, 0.95]; otherwise the config default (0.85). The gate and the
  comparison both run in the deductive space so the similarity is defined.

## References

- Guo, Pleiss, Sun, Weinberger (2017), *On Calibration of Modern Neural
  Networks* — temperature scaling, ECE.
- Angelopoulos, Bates et al. (2021), *A Gentle Introduction to Conformal
  Prediction*; Angelopoulos et al. (TACL 2024), arXiv:2405.01976 —
  multi-label conformal sets.
- Moulavi et al. (2014), *Density-Based Clustering Validation* (SDM) —
  DBCV / HDBSCAN `relative_validity_`.
- v5 §8 (deductive rules), §9 (inductive pipeline), §7 (abductive
  sequencing), §12 (G2 gate).
