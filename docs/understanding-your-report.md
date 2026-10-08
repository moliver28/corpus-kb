# Understanding your report

`corpus-kb research report` emits ONE governance artifact. The **novice**
level prints traffic lights, plain language, and next actions; the **expert**
level is the full schema. This guide walks every section in plain language.
Formulas and their sources live in [research-math.md](research-math.md).

## ISR (Incremental Sampling Rate)

The share of coding work that introduces a code no earlier batch found. A
low, flat ISR means new batches mostly reuse known codes - the corpus has
stopped teaching you anything new and the codebook is ready to freeze.
Read it together with the coverage-curve slope below.

## Deductive coverage

How many coded units sit ABOVE their code's threshold. Coverage is
threshold-driven, so it is NOT a completeness measure: lowering a threshold
raises coverage without finding anything new. Use the residual section as the
completeness radar instead.

## Residual and missing codes

The residual share R(tau_res) counts codable units that NO existing code
gets close to. A high R means the codebook is missing something. The report
also lists candidate missing codes: clusters of far-from-every-code units
that sit calmly together - a theme with no code.

## tau_res per source type

tau_res is calibrated from your gold data PER source type: it is the low
edge of how similar two units of the same code look. Interviews and meetings
differ, so the threshold differs too. If one source type's R is much worse,
add gold exemplars for that type and recalibrate.

## Cluster stability

Re-clusters the corpus many times and reports how often the same groups
reappear (mean pairwise ARI). 0.75 or higher is stable. Unstable clusters
should not be promoted as codes yet; collect more units first.

## Code overlap

Flags pairs of codes whose run-time centroids drifted closer than your gold
data ever shows them, plus the units holding both codes. Overlapping codes
confuse coders and reviewers; merge or sharpen definitions in the next
codebook version.

## Keyword conflicts

Where two codes claim the same keyword, or a positive term collides with an
exclusion term. Ambiguous keywords create ambiguous coders - resolve each
conflict by editing definitions or keyword lists.

## Keyword lists

Per-code inclusion/exclusion keyword lists synthesized from the units each
code actually captured (Monroe log-odds or c-TF-IDF). Only keyword hits
inside the ANSWER count as explicit evidence; question-only hits nominate
candidates.

## Provisional keyword lists

Lists built from fewer than 15 supporting units are marked provisional.
They are drafts: do not gate routing on them yet.

## Interrater reliability

Compares model and human coding decisions. Krippendorff's alpha below 0.667
halts codebook promotion; 0.667-0.80 marks codes tentative; 0.80+ is
reliable. Gwet's AC1 is reported beside alpha because alpha collapses on
rare codes even at high agreement - a prevalence-affected flag means the
code is rare, not bad.

## G3 audit

Checks whether the uncertainty signals actually predict reviewer overrides
(AUROC, AURC, accuracy-at-coverage) and whether model decisions match human
ones (LLM-vs-human alpha, plus calibration ECE). Weak signals are dropped
from the review priority automatically.

## Conformal set sizes

How many codes remain statistically plausible per unit at your guaranteed
coverage level. Size 1 is a confident call; larger sets route to review. A
heavy tail of large sets means scores are not separating codes - add gold or
tighten definitions.

## Run manifest

Pins every version, threshold, seed, and model used, so any report can be
reproduced byte-for-byte. If a number looks wrong, the manifest is where the
audit trail starts.

## Candidate missing codes

Groups of units that sit far from every existing code AND sit calmly
together - the report's best guess at themes your codebook does not cover.
Promote real ones with `corpus-kb codebook promote`; discard noise from the
review queue.

## Review backlog

The set of assignments waiting for a human decision. Work the queue with
`corpus-kb review accept|override`; overrides feed the next threshold refit.
