# Coding Rules

Coding turns one cell (a chunk plus up to five candidate codes) into
one decision per candidate code. These rules pin the decision shape
and the two honesty rules the coder follows, whether the coder is a
model or a person.

## The decision shape

For every (chunk, code) pair the coder produces:

- `decision`: `assign` or `reject`. No third option; "maybe" is a
  `reject` with a low confidence and an escalation route.
- `confidence`: a number from 0 to 1 for the decision just made.
- `evidence_quote`: the exact words in the chunk that carry the
  decision. An `assign` without a quote is refused; the engine's quote
  gate checks the quote is verbatim in the chunk.
- `rationale`: one or two sentences that tie the quote to the code's
  inclusion criteria.

The engine routes each decision by confidence bands:

| Band | Threshold | Route |
|---|---|---|
| at or above high | 0.90 | high-conf accept or reject |
| at or above mid | 0.70 | mid band |
| below mid, at or above low | 0.50 | escalate to review |
| below low | 0.50 | low-conf escalate (most urgent) |

Two routes cut across the bands: a failed quote gate becomes
`manual_review` whatever the confidence, and a provisional (unquoted)
assign is capped at the mid band and marked `provisional-hold`.

## The participant-words rule

Code what the participant said, in the participant's words. Concretely:

- The evidence quote is copied from the chunk, never paraphrased,
  never reconstructed from memory, never taken from another chunk.
- The rationale may use the codebook's words, but the quote carries
  the claim. If no span in the chunk supports the code, the decision
  is `reject`, whatever the topic feels like.
- A summary or an inference is never written as a quote. Cite
  summaries as summaries; cite participant statements as `[S:<source-slug>]`
  with the chunk they came from.

## The anti-priming rule

Do not tell the coder what answer you want before it decides.

- When a model codes, the window shows the chunk text and the code
  definitions from the codebook, nothing else: no expected decisions,
  no earlier coder's answers, no reliability targets.
- When a person codes, the same rule holds. Hand over the chunk and
  the codebook, not the first coder's sheet, the hypothesis, or the
  result you hope for.
- Double-coding for reliability means two independent passes. Showing
  the second coder the first coder's decisions measures obedience, not
  agreement.
- Candidate codes in a cell come from pooling (keyword and similarity
  signals), not from what the analyst believes the chunk should say.
  That is the point of `codify pool` before `codify code`.

## Batch discipline

- Code a small batch first and read the routes before scaling up. A
  batch that returns mostly escalations is telling you the codebook or
  the floors need work, not that the corpus is hard.
- Keep one coder tag per coder. Reliability compares tags; mixing tags
  blurs the measurement.
- `batch_id` labels the ledger for a run. Use it on every batch in a
  pass; saturation and stop rules read it.
