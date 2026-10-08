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
