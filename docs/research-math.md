# Research Math (v5 §8/§9/§11/§12/§13/§14 — deductive, inductive, calibration, exhaustiveness, keywords, governance)

Every formula below states its v5 source and its calibration source so
maintainers never re-derive policy from code. This file is extended as later
todos land (todo 17 owns the exhaustiveness/overlap/IRR/keyword catalog).

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

## Exhaustiveness math (v5 §11, todo 17)

- **Per-unit residual** (`research/exhaustiveness.py`): the best any existing
  code can explain a unit,
  `r_i = max over (code k, prototype p, view v ∈ {A, QA}) cos(u_i^v, proto_{k,p})`.
  Low `r_i` = the unit is far from EVERY code = candidate missing structure.
  The DB path (`analytics_sql`) is SQL-side per the MRL contract
  (Kusupati et al. 2022; r10): ANALYTICS scans retrieve candidate id sets via
  one ordered HNSW scan per prototype on the 016 `embedding_256` column with
  the pinned probe `embedding_256 <=> $1::vector(256)` (prototypes sliced to
  256 dims and re-normalized), then surfaced candidates are RE-RANKED at the
  FULL 1024-d vectors by exact cosine (ids fetched by PK) — final values are
  full-dim exact, consistent with the 1024-d tau_res calibration. The QA-view
  leg probes `research_exchanges.embedding_256` the same way and maps back to
  answer units; the RETRIEVAL access pattern uses the halfvec(1024) cast
  index with the pinned probe `embedding::halfvec(1024) <=> $1::halfvec(1024)`
  (one primary index per access pattern, r11). Recall@k delta of the 256-d
  slice vs full-dim is 0.0 — owned by the todo-13 G1 artifact
  (task13-g1-report.json). NEVER a Python-side O(n·m) full-vector scan
  (Oracle B r7); EXPLAIN on the fixture proves HNSW index usage for both
  probes.
- **Residual share** `R(τ_res) = frac(r_i < τ_res)` — the share of codable
  units NOT within the threshold of any code (v5 §11; Oracle A D1), reported
  with P10/P50/P90 of `r` per source type. Naming: `r_i` is the residual
  SIMILARITY; low means unexplained.
- **τ_res calibration (never hardcoded)**: per source type,
  `τ_res[type] = P10` of the within-code gold cosine distribution for that
  type (gold exemplars of every code, answer view, pairwise cosines; G2
  discipline), floored at 0.05 for degenerate types. Interviews and meetings
  legitimately differ; the fixture asserts the two types differ.
- **Coverage-curve slope**: OLS slope of a metric across consecutive
  checkpoints. Plateau = |slope| <= 0.01 on `R(τ_res)` and/or on
  new-codes-per-batch (the inductive runs' `meta_new` per batch). NEVER
  defined on the §8 deductive coverage — that is threshold-driven, not a
  completeness measure; it is reported separately (r8). Prior `R` values
  come from deductive runs' final checkpoints (`exhaustiveness` block,
  written by deductive_run); the live report appends the current `R`.
- **Candidate missing codes**: units with `r_i < τ_res[type]` AND
  `soft_cluster_entropy < 0.85` (or entropy-unknown — never fabricate
  certainty) are clustered (spherical k-means, k ∈ {1, 2}) and surfaced in
  the checkpoint report + the manual-review queue (`research_noise_queue`,
  cluster labels are `residual_candidate:<k>`; queue rows keep
  `status='pending'`).
- **Run-based stopping** (v5 §11): `new_codes_in_run / unique_codes_in_base
  < 5%` across the two most recent deductive runs.

## Cluster stability (todo 17)

- **Adjusted Rand Index** (`cluster_stability.ari`): contingency-table form,
  `ARI = (Σnij2 − E) / (max − E)` with `E = Σai2·Σbj2 / C(n,2)`; the noise
  label −1 is its own group. 1.0 = identical partitions.
- **Bootstrap stability**: >= 10 resamples (subsample without replacement at
  80% — the standard cluster-stability assessment), re-cluster each, mean
  pairwise ARI over shared points; **>= 0.75 = stable**. The clusterer is
  injectable: the inductive engine's HDBSCAN when the extra exists, else the
  deterministic numpy **spherical k-means** (seeded k-means++-style init,
  max-cos assignment, mean-then-renormalize updates).

## Semantic overlap (v5 §11, Oracle A D2 + Momus M-3)

- **Code-centroid matrix** `M = ĈĈᵀ` over L2-normalized per-code centroids
  in the deductive unit space (centroids = SQL `avg(embedding_256)` over each
  code's assigned units; the k×k product is trivially small — not a kNN
  scan, so no index requirement applies).
- **τ_overlap calibration**: `max inter-code gold prototype cosine + 0.05`,
  clamped [0.70, 0.95] (same policy as promote_code's τ_dup — with gold
  codes, run-time centroid pairs closer than the WORST gold between-code
  separation are overlap). Pairs with `M[i,j] > τ_overlap` are flagged for
  codebook review.
- **Shared-unit confusion matrix**: per code pair, units holding BOTH codes,
  plus units whose top-1/top-2 assignment margin < `δ_amb` (calibrated on
  the review-routing boundary, todo 16) — `combined` is the union.
- **Per-code silhouette**: mean silhouette (cosine distance) over each code's
  assigned units; singletons score 0. Cross-checks the overlap flags from
  the geometry side.
- **Duplicate-gate surfacing**: the todo-15 promote-time tau_dup blocks
  (`research_proposed_codes.block_reason`) are listed in the SAME `overlap`
  section as the flagged centroid pairs — both route to codebook review.

## Dual-coefficient IRR governance (v5 §11/§14, r8 + r10)

- **Krippendorff α bands** (accepted-practice thresholds with defined
  actions): `α >= 0.80` reliable; `0.667 <= α < 0.80` -> the code is flagged
  `tentative-reliability` in the checkpoint and routed to review;
  `α < 0.667` -> the promotion gate HALTS.
- **Gwet's AC1** (`irr_governance.gwet_ac1`): reported ALONGSIDE α per code —
  the α paradox: α collapses for rare codes even at 95% agreement.
  `AC1 = (Pa − Pe(γ)) / (1 − Pe(γ))` with `Pe(γ) = 2π(1−π)`, π the average
  marginal positive-rating probability, over the SAME `(n_u0, n_u1, m_u)`
  pairable-weighted units the vendored α uses. With raw triples absent, AC1
  is reconstructed from α under a documented symmetric-marginal
  approximation (`_ac1_from_alpha`); production always prefers real triples.
- **Prevalence-affected note** (O'Connor & Joffe 2020 §4): `α < 0.80` but
  `AC1 > 0.90` — interpretive note, NOT a halt (the code is rare, not bad).
- The report's model-vs-human sheets: every assignment row is the model's
  "applied" rating; accepted reviews are the human's applied ratings;
  overridden reviews rate the code 0; unreviewed assignments are MISSING
  data, never fabricated 0s (same rule as the vendored α).

## Keywords and exclusion keywords (v5 §13, todo 17)

- **Positive keywords** per code vs the rest (`keyword_synthesis.py`):
  Monroe informative-Dirichlet log-odds z (Monroe et al. 2008; the vendored
  `coding/keyness.log_odds_dirichlet`:
  `z = [log((a+α)/(n_a−a+α)) − log((b+α)/(n_b−b+α))] / sqrt(1/a' + 1/(n_a−a)' + 1/b' + 1/(n_b−b)')`,
  α = 0.01) or **c-TF-IDF** (BERTopic form: join each code's member texts
  into one class document; `score = tf(c,t) · log(1 + A / freq_avg(t))` with
  A = number of classes).
- **Exclusion keywords**: the same method contrasting REJECTED gray-zone /
  overridden texts against the coded corpus, widened periodically by a
  seeded random sample of uncoded units (v5 §13 contrast-widening).
- **hit_location**: each keyword hit on a member unit records
  `answer` | `question` | `both` (migration 018 `research_keyword_hits`).
  ONLY `answer` hits count as explicit evidence; question hits are candidate
  generators gated downstream by stance + information gain (v5 §13).
- **Minimum support**: ~15-20 units; below 15 the code's list is marked
  `provisional` (floor proof: a 5-unit code is provisional).
- **Conflict flags** (the MECE intent as actual mechanics): positive terms
  claimed by >1 code (`shared_positive`) and positive-vs-exclusion
  collisions (`positive_vs_exclusion`) — flagged for codebook review, never
  silently resolved.

## References

- Guo, Pleiss, Sun, Weinberger (2017), *On Calibration of Modern Neural
  Networks* — temperature scaling, ECE.
- Angelopoulos, Bates et al. (2021), *A Gentle Introduction to Conformal
  Prediction*; Angelopoulos et al. (TACL 2024), arXiv:2405.01976 —
  multi-label conformal sets.
- Moulavi et al. (2014), *Density-Based Clustering Validation* (SDM) —
  DBCV / HDBSCAN `relative_validity_`.
- Kuhn, Gal, Farquhar (ICLR 2023), arXiv:2302.09664; Farquhar et al.
  (Nature 2024) — semantic entropy, NLI clustering (todo 16).
- Geifman & El-Yaniv (2017), *Selective Classification for Deep Neural
  Networks* (NeurIPS); arXiv:2407.01032 — risk-coverage, accuracy@coverage
  (G3).
- Gwet (2008), *Computing Inter-Rater Reliability in the Presence of High
  Agreement* — AC1; O'Connor & Joffe (2020), *Interrater Reliability in
  Qualitative Research* (Frontiers in Psychology) — α/AC1 interpretation,
  prevalence effects.
- Krippendorff (2004), *Content Analysis: An Introduction to Its
  Methodology* — Krippendorff's alpha (the vendored multi-rater coefficient;
  the AC1 companion follows Gwet 2008).
- Guest, Bunce, Johnson (2006), *How Many Interviews Are Enough?* (Field
  Methods); Hennink, Kaiser, Marconi (2017), *Code Saturation versus Meaning
  Saturation* (Qual Health Res); Malterud, Siersma, Guassora (2016),
  *Information Power* (Qual Health Res) — saturation/information power
  (v5 §11 semantics).
- Nelson (2020), *Computational Grounded Theory* (Sociological Methods &
  Research); Maaravi-Hesseg et al. (2021) — TEA / computational grounded
  theory.
- Kusupati et al. (2022), *Matryoshka Representation Learning* (NeurIPS) —
  the embedding_256 analytics space + MRL truncation.
- Monroe, Colaresi, Quinn (2008), *Fightin' Words* (Political Analysis) —
  informative-Dirichlet log-odds.
- Cormack, Clarke, Buettcher (2009), *Reciprocal Rank Fusion Outperforms
  Condorcet* (SIGIR) — RRF k=60 (retrieval stack).
- Hubert & Arabie (1985) — adjusted Rand Index (cluster stability).
- v5 §8 (deductive rules), §9 (inductive pipeline), §7 (abductive
  sequencing), §11 (exhaustiveness), §12 (G2 gate), §13 (keywords),
  §14 (governance/manifest).
