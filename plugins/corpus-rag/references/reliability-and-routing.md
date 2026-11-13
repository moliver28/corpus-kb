# Reliability and Routing

The two number systems in chunk coding, and what to do with each.
Every agreement number you report travels with the epistemological
caution (see `epistemological-caution.md`).

## Reliability thresholds

`codify reliability` samples the coded corpus and computes
Krippendorff's alpha and Fleiss' kappa per code and overall.

| Range | Reading | What to do |
|---|---|---|
| 0.800 and above | reliable | The code can carry evidence claims. |
| 0.667 to 0.800 | tentative | The code routes to review until more double-coded units land. |
| below 0.667 | not reliable | Fix the code before promotion: sharpen the inclusion criteria, recalibrate floors, re-pool, and re-code. |

Rules the report follows:

- Promotion halts when a code sits below 0.667. The number is a floor,
  not a suggestion.
- Alpha collapses on rare codes even at high agreement. Read a rare
  code's alpha beside its prevalence note instead of panicking at it.
- More double-coded units is the fix for a tentative code. Reliability
  on three shared cells is noise; on thirty it is a measurement.

## Confidence bands and routes

`codify code` routes every decision by the bands (overridable with
`--bands`, which accepts only `high`, `mid`, `low`):

| Band | Default | Route |
|---|---|---|
| high | 0.90 | high-conf accept or reject |
| mid | 0.70 | mid band |
| low | 0.50 | escalate below it; below low is the most urgent queue |

Cut across the bands: a failed quote gate becomes `manual_review`,
and a provisional (unquoted) assign is capped at mid and marked
`provisional-hold`. `codify status` shows the route counts. A healthy
run has most rows in accept routes and a review queue you can actually
read.

## The manual-review query

Rows with route `manual_review` wait for a human. Count them per code,
then read them:

```bash
corpus-rag sql "SELECT code_id, COUNT(*) AS n FROM final_codes WHERE status = 'manual_review' GROUP BY code_id ORDER BY n DESC" -p <slug>
```

Read the actual rows by joining the chunk text back:

```bash
corpus-rag sql "SELECT fc.code_id, fc.chunk_id, fc.confidence, c.text FROM final_codes fc JOIN chunks c ON c.chunk_id = fc.chunk_id AND c.tenant_id = fc.tenant_id WHERE fc.status = 'manual_review' ORDER BY fc.confidence ASC LIMIT 50" -p <slug>
```

More manual-review rows mean more human reading, not worse coding.
The route exists so that uncertain evidence reaches a person instead
of silently entering the accepted set.

## Reading the two systems together

- Reliability (alpha, kappa) measures coder agreement on decisions
  already made.
- Routing (bands) decides where a single decision goes before anyone
  aggregates anything.
- A run can route cleanly and still disagree with itself. A run can
  agree with itself and still be wrong together. Report both, with the
  caution, and let the human judgment stay where it belongs.
