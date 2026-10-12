# Research subsystem

Corpus-KB's research subsystem turns interview and meeting transcripts into a
coded, queryable, audited qualitative dataset. Start with
[getting-started.md](getting-started.md); this page is the command and config
reference.

## The pipeline at a glance

1. **Ingest** transcripts: `corpus-kb research ingest-transcript <file>` for
   one file, `corpus-kb research ingest <dir|glob> [--watch]` for dynamic
   ingestion with file-hash + text-hash dedup.
2. **Build the codebook**: `corpus-kb coding inductive` proposes themes
   (optional `inductive` extra); `corpus-kb codebook promote` is the human
   gate that mints each new codebook version.
3. **Code the corpus**: `corpus-kb coding run --codebook-version-id <uuid>`
   applies the deductive v2 engine (three-view scoring, calibrated
   thresholds, conformal routing).
4. **Review**: `corpus-kb review accept|override <assignment-id>` executes
   human decisions; overrides feed threshold refits and the G3 audit.
5. **Report**: `corpus-kb research report [--level novice|expert]` emits the
   ONE governance artifact (saturation, exhaustiveness, overlap, IRR, G3).
6. **Notebook**: `corpus-kb research ask "<question>" --project-id <uuid>`
   answers with exchange-level citations; `research evidence|uncoded|overlap`
   are the analyst views.
7. **Orchestrate**: `corpus-kb research cycle` chains the steps with
   in/on/out-of-loop postures and human-critical gates.

`corpus-kb research demo` runs steps 1-6 end-to-end on a bundled mini corpus
with narration - the fastest way to see the shapes of every artifact.

## Deployment note: embeddings

The research embedding boundary requires EXACTLY 1024 dims (native or
MRL-sliced from >=1024). The default `pgml` provider abstains when the pgml
extension is absent, and 768-dim models (nomic-embed-text) always abstain:
vectors stay NULL and retrieval degrades to lexical-only. Real research
deployments should set `embedding.provider: ollama` with a >=1024-dim model
(for example `qwen3-embedding:8b-q8_0`). `corpus-kb doctor` warns about this
shape.

## Config reference

Every `research.*` key below ships in the default config
(`get_default_config` / packaged `config.yaml`). This table is pinned by a
test that parses column 1 against the defaults, so new keys cannot land
undocumented.

| Key | Default | Effect |
|-----|---------|--------|
| `research.embedder.dimensions` | `1024` | Promotion guard: research embedders must emit exactly 1024 dims; anything else halts promotion and abstains at run time. |

The research embedding strategy is not a runtime knob: the G1 experiment
(two-arm, 52-pair fixture) was decided in code — naive per-text pooling with
a deterministic prefix won, and every research embedding row records the
revision tag `"1024"`. Changing embedding behavior is a committed code
change, never a config edit.
| `research.ingest.watch_interval_s` | `10` | Poll interval (seconds) for `research ingest --watch` drop-directory tailing. |
| `research.inductive.entropy_threshold` | `0.85` | Normalized soft-entropy gate above which a unit gets the LLM "existing code vs new code" meta-decision. |
| `research.inductive.recluster_every_batches` | `8` | Full re-cluster cadence during incremental inductive growth (v5 §9.5: every 5-10 batches). |
| `research.inductive.centroid_drift_threshold` | `0.15` | Mean cosine drift between centroid snapshots that triggers an early cluster refresh. |
| `research.inductive.tau_dup` | `0.85` | Promote-time duplicate gate: max prototype cosine vs existing codes above which a promotion blocks with a merge suggestion. |
| `research.cycle.halt_on` | 7-gate list | Human-critical gates for `research cycle --mode out` (interpretive_code_review, gray_zone_escalation, drift_alarm, overlap_conflict, threshold_unreliable, human_parity_breach, unreleased_draft). May only ADD gates: `codebook_promotion` is hard-floored and never removable; unknown names are rejected. |
| `research.alt_test.margin` | see `config.yaml` | One-sided Alternative Annotator Test margin (epsilon): how much model agreement may trail human agreement and still pass (U13). |
| `research.alt_test.fdr_q` | see `config.yaml` | Benjamini-Yekutieli FDR level for the alt-test hypothesis family (U13). |
| `research.alt_test.passing_fraction` | see `config.yaml` | Fraction of held-out annotators the model must beat for the alt-test to pass (U13). |
| `research.alt_test.method` | see `config.yaml` | Alt-test p-value method: exact_binomial (default) or shifted bootstrap; numpy-only (U13). |
| `research.alt_test.n_bootstrap` | see `config.yaml` | Bootstrap resample count when alt_test.method is bootstrap (U13). |
| `research.audit.sample_rate` | see `config.yaml` | Fraction of auto-accepted units drawn for human audit after risk-coverage routing (U41). |
| `research.audit.seed` | see `config.yaml` | Seed for the stratified audit draw; recorded with the sample for reproducibility (U17). |
| `research.consensus.runs` | `3` | LIVE (U48): the cycle's inductive stage runs this many INDEPENDENT inductive passes; their proposed codes are aggregated by the release-layer multi-run consensus (`research/release/consensus.py`, wired via `research/cycle_consensus.py`) before themes surface. `1` keeps the single-pass path. |
| `research.consensus.min_run_fraction` | `0.5` | LIVE (U48): a consensus theme is kept only when candidates from at least this fraction of the runs support it. |
| `research.consensus.similarity_threshold` | `0.8` | LIVE (U48): cosine at which a candidate joins an existing consensus theme during greedy leader clustering. |
| `research.judgment_distribution.use_mean` | see `config.yaml` | Use the mean over sampled/probabilistic judgments instead of the mode (U35); off by default. |
| `research.labeling.aggregation` | see `config.yaml` | Weak-supervision aggregation strategy: em (Dawid-Skene) or vote (U38). |
| `research.labeling.min_signals` | see `config.yaml` | Minimum independent coding signals before EM aggregation is permitted; below it, majority vote with tie-to-review (U38). |
| `research.labeling.cv_folds` | see `config.yaml` | Cross-validation folds for out-of-sample label-error triage (U39). |
| `research.labeling.flag_threshold` | see `config.yaml` | Out-of-sample probability below which an assigned label is flagged to the review queue (U39). |
| `research.prompt_stability.alpha_threshold` | see `config.yaml` | Minimum agreement (krippendorff alpha) across hashed prompt variants over calibration units (U12). |
| `research.prompt_stability.min_variants` | see `config.yaml` | Prompt variants required before stability is evaluable; fewer reports not_evaluable (U12). |
| `research.release.default_profile` | see `config.yaml` | Release profile applied when none is named: exploratory | team-codebook | high-assurance (U3). |
| `research.release.enforcement` | `warn` | LIVE (U24): off \| warn \| enforce, read from config by the deduction guard (`research/deduction_guard.py`). `warn` records the active release hash or an `unreleased_draft` warning on every deductive run; `enforce` blocks unreleased codebook versions unless `--allow-draft` (bypass runs are labeled `sandbox`); `off` leaves run behavior unchanged. |
| `research.review.batch_size` | see `config.yaml` | Units per active human-review batch (U37). |
| `research.review.random_fraction` | see `config.yaml` | Stratified-random fraction always present in review batches so error can be estimated without selection bias (U37). |
| `research.review.diversity` | see `config.yaml` | Apply greedy max-min embedding diversity when selecting the active part of a review batch (U37). |
| `research.routing.target_risk` | see `config.yaml` | Target selective risk for the auto-accept threshold derived from the risk-coverage curve (U41). |
| `research.routing.confidence_level` | see `config.yaml` | Confidence level for the Clopper-Pearson upper bound guarding the routing threshold (U41). |
| `research.routing.min_calibration_n` | see `config.yaml` | Minimum calibration labels before thresholding is evaluable; below it everything routes to review (U41). |
| `research.release.profiles.exploratory.required_gates` | profile value | exploratory profile (no gates required; uncorrected results labeled honestly); Gate ids that must be pass-or-waived before release. |
| `research.release.profiles.exploratory.alpha_tentative` | profile value | exploratory profile (no gates required; uncorrected results labeled honestly); Agreement alpha at which results are tentative. |
| `research.release.profiles.exploratory.alpha_reliable` | profile value | exploratory profile (no gates required; uncorrected results labeled honestly); Agreement alpha at which results are reliable. |
| `research.release.profiles.exploratory.min_audit_sample` | profile value | exploratory profile (no gates required; uncorrected results labeled honestly); Minimum audited units required. |
| `research.release.profiles.exploratory.min_stratum_size` | profile value | exploratory profile (no gates required; uncorrected results labeled honestly); Minimum units per stratum for per-stratum metrics. |
| `research.release.profiles.exploratory.partition_rule` | profile value | exploratory profile (no gates required; uncorrected results labeled honestly); Partition disjointness rule (none | disjoint). |
| `research.release.profiles.team-codebook.required_gates` | profile value | team-codebook profile (default): shared-team evidence bar; Gate ids that must be pass-or-waived before release. |
| `research.release.profiles.team-codebook.alpha_tentative` | profile value | team-codebook profile (default): shared-team evidence bar; Agreement alpha at which results are tentative. |
| `research.release.profiles.team-codebook.alpha_reliable` | profile value | team-codebook profile (default): shared-team evidence bar; Agreement alpha at which results are reliable. |
| `research.release.profiles.team-codebook.min_audit_sample` | profile value | team-codebook profile (default): shared-team evidence bar; Minimum audited units required. |
| `research.release.profiles.team-codebook.min_stratum_size` | profile value | team-codebook profile (default): shared-team evidence bar; Minimum units per stratum for per-stratum metrics. |
| `research.release.profiles.team-codebook.partition_rule` | profile value | team-codebook profile (default): shared-team evidence bar; Partition disjointness rule (none | disjoint). |
| `research.release.profiles.team-codebook.per_code_f1_floor` | profile value | team-codebook profile (default): shared-team evidence bar; Held-out per-code F1 floor. |
| `research.release.profiles.team-codebook.content_stability_floor` | profile value | team-codebook profile (default): shared-team evidence bar; Cluster content-stability floor (top-term Jaccard). |
| `research.release.profiles.team-codebook.prompt_stability_alpha` | profile value | team-codebook profile (default): shared-team evidence bar; Required prompt-stability alpha. |
| `research.release.profiles.team-codebook.alt_test_margin` | profile value | team-codebook profile (default): shared-team evidence bar; Alt-test margin. |
| `research.release.profiles.team-codebook.alt_test_passing_fraction` | profile value | team-codebook profile (default): shared-team evidence bar; Alt-test passing fraction. |
| `research.release.profiles.team-codebook.max_stratum_disparity` | profile value | team-codebook profile (default): shared-team evidence bar; Maximum allowed per-stratum metric disparity. |
| `research.release.profiles.team-codebook.agreement_routing_threshold` | profile value | team-codebook profile (default): shared-team evidence bar; Cross-run agreement score at/above which units may auto-accept. |
| `research.release.profiles.high-assurance.required_gates` | profile value | high-assurance profile: full evidence incl. audit sample; Gate ids that must be pass-or-waived before release. |
| `research.release.profiles.high-assurance.alpha_tentative` | profile value | high-assurance profile: full evidence incl. audit sample; Agreement alpha at which results are tentative. |
| `research.release.profiles.high-assurance.alpha_reliable` | profile value | high-assurance profile: full evidence incl. audit sample; Agreement alpha at which results are reliable. |
| `research.release.profiles.high-assurance.min_audit_sample` | profile value | high-assurance profile: full evidence incl. audit sample; Minimum audited units required. |
| `research.release.profiles.high-assurance.min_stratum_size` | profile value | high-assurance profile: full evidence incl. audit sample; Minimum units per stratum for per-stratum metrics. |
| `research.release.profiles.high-assurance.partition_rule` | profile value | high-assurance profile: full evidence incl. audit sample; Partition disjointness rule (none | disjoint). |
| `research.release.profiles.high-assurance.per_code_f1_floor` | profile value | high-assurance profile: full evidence incl. audit sample; Held-out per-code F1 floor. |
| `research.release.profiles.high-assurance.content_stability_floor` | profile value | high-assurance profile: full evidence incl. audit sample; Cluster content-stability floor (top-term Jaccard). |
| `research.release.profiles.high-assurance.prompt_stability_alpha` | profile value | high-assurance profile: full evidence incl. audit sample; Required prompt-stability alpha. |
| `research.release.profiles.high-assurance.alt_test_margin` | profile value | high-assurance profile: full evidence incl. audit sample; Alt-test margin. |
| `research.release.profiles.high-assurance.alt_test_passing_fraction` | profile value | high-assurance profile: full evidence incl. audit sample; Alt-test passing fraction. |
| `research.release.profiles.high-assurance.max_stratum_disparity` | profile value | high-assurance profile: full evidence incl. audit sample; Maximum allowed per-stratum metric disparity. |
| `research.release.profiles.high-assurance.agreement_routing_threshold` | profile value | high-assurance profile: full evidence incl. audit sample; Cross-run agreement score at/above which units may auto-accept. |

## Optional extras

- `pip install -e ".[latechunk]"` - late-chunking embedder (G1 A/B loser on
  the pinned fixture; kept as the causal reference arm).
- `pip install -e ".[inductive]"` - UMAP/HDBSCAN clustering for the
  inductive engine.

Both are optional: `corpus-kb doctor` reports their status and the core
pipeline never requires them.

## Research cycle

`corpus-kb research cycle` (todo 20) is pure orchestration over the step
commands: ingest -> inductive -> deductive -> keywords -> report ->
notebook. It contains no new analytics and never auto-promotes a codebook.

### Postures

- `--mode in` executes exactly ONE next stage, then exits (fully in the
  loop).
- `--mode on` executes the next stage, then halts for explicit approval
  before continuing (CLI y/N prompt; the MCP tool returns an
  `awaiting_approval` receipt and continues when called again with
  `approve=true`).
- `--mode out` runs unattended through the chain and halts ONLY at the
  human-critical gates in `research.cycle.halt_on`.
- `--watch <dir>` is a foreground poll: when new transcripts land in the
  drop directory, the full out-mode cycle re-arms (idle = sleep, no
  daemon, no busy-loop; a file is picked up exactly once via the
  file-hash ledger).

### Gates and resuming

Each halt prints `AWAITING HUMAN: <gate>` plus the exact next command,
with a distinct per-gate exit code (10-16). `codebook_promotion` is
HARD-FLOORED: config may add gates to `halt_on` but can never remove the
floor, and promotion itself always runs through
`corpus-kb codebook promote`. A denied on-mode approval exits 20; a
cycle that cannot proceed (e.g. no codebook version yet) exits 2.

Progress is event-sourced: the cycle IS a `CodingRun` with
`params.kind='cycle'`, and each stage boundary lands a checkpoint. To
resume, act on the gate, then re-run the same command - the new cycle
run continues after the last checkpoint and never re-halts on the SAME
gate it halted on.

The `gray_zone_escalation` gate is CONVERGENT: it fires on review-queue
items that still await a decision (plus conformal-coverage violations
from the current run), not on the raw volume that routed to review.
Once every escalation is accepted or overridden
(`corpus-kb review accept|override`), the gate no longer holds the
cycle and the resumed run completes with exit 0 and the report artifact.
A later cycle that routes NEW units to review halts again — each
escalation batch gets exactly one human pass.

`--guide` upgrades stages and halts into taught decisions (what is
happening, what to look at, proposed codes with example units, and
promote-vs-skip consequences); `--json` emits one machine-readable
stage/gate event per line for agents.

## Release lifecycle (U5, v6 §6)

`corpus-kb release ...` walks one codebook version from draft to released
codebook. ONE handler per operation (`handlers/release_handlers.py`)
backs all three transports — the CLI commands, the `release_*` MCP tools,
and the `/api/release/*` HTTP routes — so the payloads are identical
across surfaces and every command takes `--json`.

The lifecycle: `request` opens a `draft_candidate` release pinned to the
current codebook content hash; `assess` composes EVERY registered gate
over the evidence the read models can supply and WRITES NOTHING;
`review` renders the packet (per-gate evidence, failing +
not_evaluable gates, waivers, the exact next commands); `release`
re-evaluates, records the gate results on the draft, applies any
explicit `--waive GATE=justification`, and executes the transition with a
REQUIRED `--approver`; `status` and `compare` read the trail.

Honesty contract (v6 §6):

- `assess` never fails on `not_evaluable` gates — it REPORTS them (exit 0)
  beside `data_sufficiency` flags that say which evidence blocks were
  present; a gate never upgrades missing data into a pass.
- `release` refusal is structured, not a crash: under
  `enforcement=enforce` an unmet required gate set exits 1 with the
  `blocked` payload (`unmet_required_gates` + reason); default
  `enforcement=warn` releases WITH explicit warnings. Profiles
  (exploratory / team-codebook / high-assurance) and enforcement live in
  the `research.release.*` config block.
- `status` never invents state: no release row is `pre_draft`, and
  `recommended_next_action` is always a LITERAL runnable
  `corpus-kb release ...` command for the current stage
  (`current_stage`, `release_state`, `blocking_criteria`,
  `pending_change_proposals`, `data_sufficiency`).

Monitoring change proposals (U25) render on `release status` as
`pending_change_proposals`: successors of a RELEASED parent, created by
the cycle's alarms — the parent's released state, manifest hash, and
codebook hash are never mutated.
