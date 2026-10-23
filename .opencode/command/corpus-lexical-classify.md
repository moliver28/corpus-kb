---
description: "TF-IDF + logistic labels trained on the stored weak labels"
---
# corpus-lexical-classify — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: labels (method 15).
3. Check the inputs it needs: raw_text, labels (a labeling build).
4. Budget the calibration effort (moderate) and autonomy (automated_with_audit).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb lexical-classify [--chunk-id <id> ...]
  ```

- Cautions: shortcut words can carry the fit. Labels can attach to names, groups, or formatting instead of the concept.
- Validation: treat this as the baseline. Inspect errors. Tune features without the final test set.
- Claim only: baseline performance on independent test records.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb lexical-classify --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
