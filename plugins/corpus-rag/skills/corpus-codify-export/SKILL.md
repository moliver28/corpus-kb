---
name: corpus-codify-export
description: Report and export coding results. Use when the user says "alpha report", "export coded evidence", or "write up coding results", wants the reliability numbers with their caution, or needs the coded evidence as CSV.
---

# Corpus Codify Export

Write up chunk-coding results: the reliability report with its caution,
saturation as a provisional signal, and the coded evidence as CSV.

## Steps

1. Run reliability for the study:

   ```bash
   corpus-rag codify reliability -p <slug>
   ```

   Show this caution beside every alpha or kappa number in the report:

> **Caution:** alpha and kappa measure how much coders agree. They do not say whether a code is true, whether the quotes are honest, or whether the codebook fits the corpus. A high number can hide a shared mistake; a low number can mean the material is rich. Treat the number as one input; the judgment stays with you.

   Read the numbers against the thresholds in
   `../../references/reliability-and-routing.md`: at or above 0.800 is
   reliable, 0.667 to 0.800 is tentative, below 0.667 halts promotion.

2. Handle the reflexive refusal honestly. When the command refuses
   because the study is reflexive, report that the study does not use
   inter-coder reliability, and describe the coding evidence by other
   means: coverage, saturation, and the researcher's own audit. Do not
   compute agreement numbers on the side.

3. Report saturation as provisional:

   ```bash
   corpus-rag codify saturation -p <slug> --batch-id batch-001
   ```

   Write "the new-code rate fell below the threshold for this batch",
   never "the corpus is exhausted".

4. Export the coded evidence:

   ```bash
   corpus-rag codify export -p <slug> --format csv
   ```

   The rows hold the accepted codes joined with chunk text and document
   source. Redirect the output into a file for the user. For JSON
   instead, drop the format flag.

5. Run the final checklist before you call the write-up done:
   - the reliability numbers each carry the caution;
   - saturation reads as provisional;
   - the export holds only accepted codes, and the row count matches
     `codify status`;
   - every participant quote in the write-up comes from the export or
     from `corpus-rag sql` reads, never from memory;
   - the study, codebook version id, and coder tags are named in the
     write-up.

## Notes

- The export joins the accepted codes with chunk text; it is evidence
  for claims, not a transcript. Cite sources as `[S:<source-slug>]`.
- For per-code coverage and route counts, use
  `corpus-rag codify status -p <slug>` and quote its numbers as they
  print.
- `corpus-rag sql "SELECT code_id, COUNT(*) AS n FROM final_codes WHERE status = 'manual_review' GROUP BY code_id" -p <slug>`
  counts the manual-review rows per code. More manual-review rows mean
  more human reading, not worse coding.
