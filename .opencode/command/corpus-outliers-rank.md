---
description: "Chunks ranked by k-th-nearest-neighbor distance"
---
# corpus-outliers-rank — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: errors (method 30).
3. Check the inputs it needs: raw_text, embeddings.
4. Budget the calibration effort (moderate) and autonomy (human_in_loop).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb outliers-rank [--k N] [--limit N]
  ```

- Cautions: results depend on the representation and the definition of normality. Minority language can look anomalous.
- Validation: define the reference group. Inspect the flagged records. Compare detectors when the stakes are high.
- Claim only: a ranked review list with the detector and reference recorded.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb outliers-rank --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
