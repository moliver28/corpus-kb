"""Teaching copy for the research report (todo 17, r12/r13).

PURITY PIN: stdlib-only MODULE-LEVEL STRING CONSTANTS -- no functions, no
conditionals, no logic. Every plain-language string the report surface
prints lives HERE; the sole-source grep scopes to src/corpus_kb/ (docs and
tests excluded). Todo-19 demo narration and todo-20 guided halts reuse
these constants instead of duplicating prose.
"""

REPORT_TITLE = "Research governance report"
REPORT_SUBTITLE = "One artifact for checkpoints, exhaustiveness, and codebook health."

ISR_LIGHT_GREEN = (
    "GREEN: the code discovery rate is flattening - new batches mostly reuse known codes."
)
ISR_LIGHT_YELLOW = (
    "YELLOW: new codes are still appearing - keep iterating before freezing the codebook."
)
ISR_PLAIN = (
    "ISR (inductive saturation ratio) is the share of coding work that "
    "introduces a code no earlier batch found. A low, flat ISR means the "
    "codebook has stopped growing."
)
ISR_ACTION = (
    "Check the coverage-curve slope below; if it is flat too, the corpus "
    "has stopped teaching you anything new."
)

COVERAGE_PLAIN = (
    "Deductive coverage is how many coded units sit ABOVE their code's "
    "threshold. It is threshold-driven, so it is NOT a completeness "
    "measure - the residual section below is the completeness radar."
)
COVERAGE_ACTION = (
    "Codes with zero explicit coverage usually need better gold exemplars or a lower threshold."
)

RESIDUAL_PLAIN = (
    "The residual share R(tau_res) counts codable units that NO existing "
    "code gets close to. A high R means the codebook is missing something."
)
RESIDUAL_LIGHT_GREEN = "GREEN: nearly every unit is within reach of an existing code."
RESIDUAL_LIGHT_YELLOW = (
    "YELLOW: a noticeable share of units is unexplained - review the candidate missing codes."
)
RESIDUAL_LIGHT_RED = (
    "RED: many units are far from every code - the codebook likely misses whole themes."
)
RESIDUAL_ACTION = (
    "Read the candidate missing codes, then run corpus-kb coding inductive "
    "on them to draft new code proposals."
)

TAU_RES_PLAIN = (
    "tau_res is calibrated from your gold data per source type: it is the "
    "low edge of how similar two units of the same code look. Interviews "
    "and meetings differ, so the threshold differs too."
)
TAU_RES_ACTION = (
    "If a source type's R is much worse, add gold exemplars for that source type and recalibrate."
)

STABILITY_PLAIN = (
    "Cluster stability re-clusters the corpus many times and asks how "
    "often the same groups reappear (mean pairwise ARI). 0.75 or higher "
    "is stable."
)
STABILITY_LIGHT_GREEN = "GREEN: the clusters are stable across resamples."
STABILITY_LIGHT_YELLOW = (
    "YELLOW: cluster boundaries move between resamples - treat cluster membership as provisional."
)
STABILITY_ACTION = (
    "Unstable clusters should not be promoted as codes yet; collect more units first."
)

OVERLAP_PLAIN = (
    "Code overlap flags pairs of codes whose run-time centroids drifted "
    "closer than your gold data ever shows them. These pairs confuse "
    "coders and reviewers."
)
OVERLAP_LIGHT_GREEN = "GREEN: no code pair is closer than gold separation allows."
OVERLAP_LIGHT_RED = (
    "RED: at least one code pair overlaps - consider merging or re-defining one of them."
)
OVERLAP_ACTION = (
    "Open the flagged pairs and their shared units, then merge or sharpen "
    "definitions in the next codebook version."
)

CONFLICT_PLAIN = (
    "Keyword conflicts show where two codes claim the same term, or a "
    "code's positive term collides with an exclusion term. Ambiguous "
    "keywords create ambiguous coders."
)
CONFLICT_ACTION = (
    "Resolve each conflict by editing the code definitions or keyword "
    "lists in the next codebook version."
)

KEYWORDS_PLAIN = (
    "Keyword lists are synthesized from the units each code actually "
    "captured (Monroe log-odds or c-TF-IDF). Only hits inside the ANSWER "
    "count as explicit evidence; question-only hits only nominate "
    "candidates."
)
KEYWORDS_PROVISIONAL = (
    "Provisional lists (fewer than 15 supporting units) are drafts - do not gate on them yet."
)
KEYWORDS_ACTION = (
    "Review flagged conflicts and provisional lists before relying on keyword routing."
)

IRR_PLAIN = (
    "Interrater reliability compares model and human coding decisions. "
    "Krippendorff's alpha below 0.667 halts codebook promotion; 0.667-0.80 "
    "marks codes tentative; 0.80+ is reliable. Gwet's AC1 is reported "
    "beside alpha because alpha collapses on rare codes even at high "
    "agreement."
)
IRR_LIGHT_GREEN = "GREEN: every assessed code is reliable (alpha >= 0.80)."
IRR_LIGHT_YELLOW = (
    "YELLOW: at least one code is tentative - it routes to review until double-coded."
)
IRR_LIGHT_RED = (
    "RED: at least one code is below the 0.667 floor - promotion halts until it is reworked."
)
IRR_ACTION = (
    "Codes flagged tentative-reliability need more double-coded units; "
    "codes flagged prevalence-affected are rare, not bad - read the note, "
    "do not panic."
)

G3_PLAIN = (
    "The G3 audit checks whether the uncertainty signals actually predict "
    "reviewer overrides (AUROC), and whether model decisions match human "
    "ones (LLM-vs-human alpha). Weak signals are dropped automatically."
)
G3_ACTION = (
    "Signals on the drop list will stop feeding the review priority; "
    "re-run the audit after the next review batch."
)

CONFORMAL_PLAIN = (
    "Conformal set sizes show how many codes remain plausible per unit at "
    "your guaranteed coverage level. Size 1 is a confident call; larger "
    "sets route to review."
)
CONFORMAL_ACTION = (
    "A heavy tail of large sets means the scores are not separating codes "
    "- add gold or tighten definitions."
)

MANIFEST_PLAIN = (
    "The run manifest pins every version, threshold, seed, and model used, "
    "so any report can be reproduced byte-for-byte."
)

MISSING_CODES_PLAIN = (
    "Candidate missing codes are groups of units that sit far from every "
    "existing code AND sit calmly together - a theme with no code."
)
MISSING_CODES_ACTION = (
    "Promote real ones with corpus-kb codebook promote; discard noise from the review queue."
)

BACKLOG_PLAIN = "The review backlog is the set of assignments waiting for a human decision."
BACKLOG_ACTION = (
    "Work the queue with corpus-kb review accept|override; overrides feed the next threshold refit."
)

NEXT_ACTIONS_HEADER = "NEXT ACTIONS"

NEXT_ACTION_FOOTER = (
    "Next actions are derived from the flags above; each maps to an existing corpus-kb surface."
)

NOTEBOOK_EVIDENCE_HEADER = "EVIDENCE"
NOTEBOOK_EMPTY_HEADER = "NO EVIDENCE"
NOTEBOOK_EVIDENCE_FOR = "Evidence for"
NOTEBOOK_UNCODED_HEADER = "UNCODED UNITS (farthest from every code first)"
NOTEBOOK_OVERLAP_HEADER = "CODE OVERLAP"

LEVEL_NOVICE = "novice"
LEVEL_EXPERT = "expert"

# ---------------------------------------------------------------------------
# Todo-19: `corpus-kb research demo` narration. Every stage prints its
# explain + doc pointer from HERE; tests assert the wiring offline.
# ---------------------------------------------------------------------------

DEMO_TITLE = "Corpus-KB research demo"
DEMO_INTRO = (
    "This narrated run takes the bundled two-interview corpus through the "
    "full qualitative-coding pipeline: ingest, codebook, coding, review, "
    "report, and a cited notebook answer. Re-runs are safe: file and text "
    "hashes make already-ingested files no-ops."
)
DEMO_DOC = "docs/getting-started.md"

DEMO_STAGE_INGEST = (
    "INGEST: each transcript is parsed into speaker turns, roles are mapped "
    "(the question-asker becomes the moderator), and turns are linked into "
    "question-answer exchanges with stance labels."
)
DEMO_STAGE_INGEST_DOC = "docs/getting-started.md#ingest-your-first-transcript"
DEMO_STAGE_CODEBOOK = (
    "CODEBOOK: the demo seeds a deductive codebook whose gold exemplars are "
    "quoted verbatim from the bundled transcripts. In a real project you "
    "build this from gold data and promote proposed codes yourself "
    "(corpus-kb codebook promote)."
)
DEMO_STAGE_CODEBOOK_DOC = "docs/getting-started.md#build-a-codebook"
DEMO_STAGE_CODING = (
    "CODING RUN: every codable unit is scored against the codebook in three "
    "views (answer, question+answer, question). With fewer than 20 gold "
    "exemplars per code the thresholds are flagged unreliable, so assignments "
    "route to the review queue - that is the methodology working, not a bug."
)
DEMO_STAGE_CODING_DOC = "docs/getting-started.md#run-coding"
DEMO_STAGE_REVIEW = (
    "REVIEW: one assignment is confirmed through the human gate "
    "(corpus-kb review accept). Overrides feed threshold refits and the G3 audit."
)
DEMO_STAGE_REVIEW_DOC = "docs/getting-started.md#review-assignments"
DEMO_STAGE_REPORT = (
    "REPORT: the governance report condenses saturation, exhaustiveness, "
    "overlap, and reliability into ONE artifact; the novice level prints "
    "traffic lights with links into the report guide."
)
DEMO_STAGE_REPORT_DOC = "docs/understanding-your-report.md"
DEMO_STAGE_ASK = (
    "NOTEBOOK: one question is answered against the coded corpus; every "
    "answer sentence carries an exchange-level citation you can open."
)
DEMO_STAGE_ASK_DOC = "docs/getting-started.md#ask-the-notebook"
DEMO_OUTRO = (
    "Demo complete. Read docs/getting-started.md to run the same pipeline on "
    "your first real transcript."
)
DEMO_NO_EMBEDDINGS = (
    "Demo cannot run: the configured embedder would abstain (research needs "
    "exactly 1024 dims), so no unit can be scored and no assignment would "
    "reach review. Fix: set embedding.provider to ollama with a >=1024-dim "
    "model (e.g. qwen3-embedding:8b-q8_0), then re-run the demo."
)

# Report doc anchors (todo-19 (g)): one per novice-view plain_language
# section, rendered as <REPORT_DOC_PATH>#<anchor>.
REPORT_DOC_PATH = "docs/understanding-your-report.md"
REPORT_DOC_ANCHORS = {
    "isr": "isr-inductive-saturation-ratio",
    "coverage": "deductive-coverage",
    "residual": "residual-and-missing-codes",
    "tau_res": "tau_res-per-source-type",
    "stability": "cluster-stability",
    "overlap": "code-overlap",
    "conflicts": "keyword-conflicts",
    "keywords": "keyword-lists",
    "keywords_provisional": "provisional-keyword-lists",
    "irr": "interrater-reliability",
    "g3": "g3-audit",
    "conformal": "conformal-set-sizes",
    "manifest": "run-manifest",
    "missing_codes": "candidate-missing-codes",
    "backlog": "review-backlog",
}

# ---------------------------------------------------------------------------
# Todo-20: `corpus-kb research cycle` narration + TAUGHT gates (r12).
# Same purity pin as above: module-level string constants ONLY. Every
# cycle stage narration, halt message, and decision-context header the
# cycle surface prints lives HERE.
# ---------------------------------------------------------------------------

CYCLE_TITLE = "Research cycle"
CYCLE_INTRO = (
    "One command walks the full pipeline - ingest, inductive pass, "
    "deductive coding, keywords, report - and stops for you at the "
    "human-critical gates. Promotion is never automated; the cycle halts "
    "and waits."
)
CYCLE_DOC = "docs/research.md#research-cycle"

CYCLE_STAGE_INGEST = (
    "INGEST: transcripts in the drop directory are parsed into speaker "
    "turns, roles, and question-answer exchanges; file and text hashes "
    "make repeats no-ops, so only NEW material is processed."
)
CYCLE_LOOK_INGEST = "Look at: the ingest receipt - how many files were new vs skipped."
CYCLE_STAGE_INGEST_DOC = "docs/getting-started.md#ingest-your-first-transcript"

CYCLE_STAGE_INDUCTIVE = (
    "INDUCTIVE PASS: every answer unit is summarized to an atomic "
    "observation, embedded, and clustered; calm, coherent groups become "
    "PROPOSED codes - drafts only, never part of the codebook yet."
)
CYCLE_LOOK_INDUCTIVE = (
    "Look at: the proposed labels and their member counts; cluster "
    "stability tells you whether each group would survive a re-cluster."
)
CYCLE_STAGE_INDUCTIVE_DOC = "docs/getting-started.md#build-a-codebook"

CYCLE_STAGE_DEDUCTIVE = (
    "DEDUCTIVE RUN: every codable unit is scored against the codebook in "
    "three views; calibrated thresholds decide, and anything uncertain "
    "routes to the review queue instead of being forced."
)
CYCLE_LOOK_DEDUCTIVE = (
    "Look at: the explicit vs question-dependent split and how much went "
    "to review - a large review share usually means thin gold, not a bad model."
)
CYCLE_STAGE_DEDUCTIVE_DOC = "docs/getting-started.md#run-coding"

CYCLE_STAGE_KEYWORDS = (
    "KEYWORDS: each code gets the terms its own captured units actually "
    "use (log-odds ranked); only hits inside ANSWER turns count as "
    "explicit evidence, and collisions are flagged as conflicts."
)
CYCLE_LOOK_KEYWORDS = (
    "Look at: conflicts (two codes claiming one term) and provisional "
    "lists with fewer than 15 supporting units."
)
CYCLE_STAGE_KEYWORDS_DOC = "docs/understanding-your-report.md#keyword-lists"

CYCLE_STAGE_REPORT = (
    "REPORT: saturation, exhaustiveness, overlap, reliability, and the "
    "G3 audit condense into ONE artifact - the single thing to read "
    "after each cycle."
)
CYCLE_LOOK_REPORT = (
    "Look at: the traffic lights first, then every RED or YELLOW section "
    "before deciding what the next cycle should change."
)
CYCLE_STAGE_REPORT_DOC = "docs/understanding-your-report.md"

CYCLE_STAGE_NOTEBOOK = (
    "NOTEBOOK: one question is answered against the coded corpus; every "
    "answer sentence carries an exchange-level citation you can open."
)
CYCLE_LOOK_NOTEBOOK = (
    "Look at: whether the citations actually support the answer - if "
    "they do not, the codebook, not the model, needs work."
)
CYCLE_STAGE_NOTEBOOK_DOC = "docs/getting-started.md#ask-the-notebook"

CYCLE_HALT_HEADER = "AWAITING HUMAN: {gate}"

CYCLE_PROPOSALS_HEADER = "PROPOSED CODES awaiting your decision"
CYCLE_PROPOSAL_LINE = "- [{status}] #{proposed_id} {label} (members: {n_members})"
CYCLE_PROPOSAL_EXAMPLES_HEADER = "  example units:"
CYCLE_PROPOSAL_EXAMPLE_LINE = "  - {text}"

GATE_CODEBOOK_PROMOTION_TAUGHT = (
    "This is the promotion gate: the model clustered units into candidate "
    "codes, but v5 codebook governance says a cluster becomes a code only "
    "when a human names it, defines it, and owns the consequences. Your "
    "definition becomes the rubric every future unit is scored against."
)
GATE_CODEBOOK_PROMOTION_LOOK = (
    "For each proposal: read the example units, check the cluster is ONE "
    "coherent idea (not two themes stapled together), and compare it "
    "against existing codes for overlap."
)
GATE_CODEBOOK_PROMOTION_PROMOTE_CONSEQUENCE = (
    "If you promote: the code enters the next codebook version, its member "
    "units become its gold exemplars, and the next deductive run scores "
    "against it. A vague definition now quietly mislabels data later."
)
GATE_CODEBOOK_PROMOTION_SKIP_CONSEQUENCE = (
    "If you skip: the cluster stays a proposal - nothing is lost. The "
    "theme simply stays uncoded and keeps surfacing in the residual "
    "(missing-code) radar until you promote it or discard it."
)
GATE_CODEBOOK_PROMOTION_ACTION = (
    "run corpus-kb codebook promote --proposed-id <id> --name <name> "
    "--definition <definition> for each code you accept (duplicates are "
    "blocked with a merge suggestion), then re-run corpus-kb research "
    "cycle --mode out to continue"
)

GATE_INTERPRETIVE_TAUGHT = (
    "Interpretive codes depend on judgment calls a lexical rule cannot "
    "reliably make, so the engine routes their assignments to review by "
    "design. The gate makes that visible instead of hiding it in counts."
)
GATE_INTERPRETIVE_LOOK = "Look at: the interpretive code ids and their review-queue volume."
GATE_INTERPRETIVE_ACTION = (
    "work the queue with corpus-kb review accept|override; keep a code "
    "interpretive only while human judgment is genuinely required"
)

GATE_GRAY_ZONE_TAUGHT = (
    "Assignments land in the gray zone when the calibrated evidence does "
    "not separate codes cleanly (or the conformal guarantee slipped). "
    "These are exactly the units where automatic answers would be guesses."
)
GATE_GRAY_ZONE_LOOK = (
    "Look at: the review count and the conformal set sizes - large sets "
    "mean the thresholds or gold need work, not that reviewers do."
)
GATE_GRAY_ZONE_ACTION = (
    "work the queue with corpus-kb review accept|override; overrides feed the next threshold refit"
)

GATE_DRIFT_TAUGHT = (
    "Cluster validity (DBCV) dropped more than 20% versus the previous "
    "run: the thematic structure moved underneath you. Codes promoted "
    "from the earlier clustering may no longer match the corpus."
)
GATE_DRIFT_LOOK = "Look at: dbcv_relative_validity vs its baseline in the run summary."
GATE_DRIFT_ACTION = (
    "re-read the current proposals and the report's cluster-stability "
    "section before trusting promoted codes; re-run the cycle once decided"
)

GATE_OVERLAP_TAUGHT = (
    "Two codes drifted so close that your own gold never shows codes that "
    "similar, or keyword lists collide. Overlapping codes produce "
    "unstable assignments and reviewer disagreement."
)
GATE_OVERLAP_LOOK = (
    "Look at: the report's flagged pairs with their shared units, and the keyword conflicts."
)
GATE_OVERLAP_ACTION = (
    "merge or re-define one of each flagged pair in the next codebook "
    "version, then re-run the cycle"
)

GATE_THRESHOLD_TAUGHT = (
    "A code has fewer than 20 gold exemplars, so its thresholds are "
    "provisional: nobody has measured where its decision boundary really "
    "sits. The engine routes these codes' assignments to review."
)
GATE_THRESHOLD_LOOK = "Look at: which code ids are flagged and how much went to review."
GATE_THRESHOLD_ACTION = (
    "double-code more units for the flagged codes (corpus-kb review "
    "accept|override) and recalibrate before trusting auto-accept"
)

GATE_PARITY_TAUGHT = (
    "The G3 audit measured the model agreeing with humans below the 0.60 "
    "alpha floor on at least one code: the model is not safe to route "
    "that code automatically, so it flips to human-only routing."
)
GATE_PARITY_LOOK = (
    "Look at: the report's g3_audit human_parity rows - the breached "
    "codes and their llm vs human alphas."
)
GATE_PARITY_ACTION = (
    "rework the breached code (definition, gold, or retire it); the flip "
    "to human-only routing is already in effect"
)

CYCLE_APPROVAL_STAGE_HEADER = "STAGE COMPLETE: {stage}"
CYCLE_APPROVAL_PROMPT = "Continue to the next stage? [y/N] "
CYCLE_APPROVAL_DENIED = (
    "Stopped at your request. Progress is recorded; re-run corpus-kb "
    "research cycle --mode on to continue from here."
)
CYCLE_RESUME_HINT = "Resuming after stage: {stage} (cycle run {run_id})"
CYCLE_WATCH_IDLE = "watch: no new transcripts; sleeping {interval}s (Ctrl-C to stop)"
CYCLE_WATCH_ARMED = "watch: {n} new transcript(s) - re-arming the cycle"
CYCLE_OUTRO = (
    "Cycle complete. The report above is the artifact to read next; "
    "re-run the cycle as new transcripts land."
)
CYCLE_NO_QUESTION = "notebook stage skipped (no --question given)"
CYCLE_INDUCTIVE_UNAVAILABLE = (
    'the inductive extra is not installed (pip install -e ".[inductive]"); '
    "the cycle stops before the promotion gate because there is nothing to promote"
)
CYCLE_NO_EMBEDDINGS = (
    "Cycle cannot run: the configured embedder would abstain (research needs "
    "exactly 1024 dims), so units cannot be scored and every stage would "
    "degrade. Fix: set embedding.provider to ollama with a >=1024-dim model "
    "(e.g. qwen3-embedding:8b-q8_0), then re-run the cycle."
)

# ---------------------------------------------------------------------------
# F3 fix round: honest inductive no-op + DB remediation hints shared by the
# CLI error handler and the installer. Same purity pin: string constants only.
# ---------------------------------------------------------------------------

INDUCTIVE_ALL_NOISE = (
    "No stable clusters: all {n} units were classed as noise - the corpus is "
    "too small or too homogeneous for UMAP/HDBSCAN to separate themes. "
    "Nothing was proposed. Add more (and more varied) transcripts, tune "
    "research.inductive, or seed the codebook directly from gold exemplars "
    "as the demo does."
)

DB_PERMISSION_DENIED_HINT = (
    "fix: as the database superuser run GRANT CREATE ON SCHEMA public TO "
    "corpus_user; GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO "
    "corpus_user; GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "
    "corpus_user; (PostgreSQL 15+ revokes CREATE on public by default - "
    "see docs/INSTALL.md step 2)"
)

LIBPQ_TOO_OLD_HINT = (
    "fix: pip install psycopg-binary (it bundles libpq >= 14; the system "
    "libpq is too old for the event store's pipeline mode - see docs/INSTALL.md)"
)
