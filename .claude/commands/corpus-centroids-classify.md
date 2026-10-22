---
description: "Nearest-centroid labels with recorded abstentions"
argument-hint: "[--abstain-margin F] [--chunk-id <id> ...]"
---
# corpus-centroids-classify — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: labels (method 13).
3. Check the inputs it needs: embeddings, labels.
4. Budget the calibration effort (low) and autonomy (automated_with_audit).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb centroids-classify [--abstain-margin F] [--chunk-id <id> ...]
  ```

- Cautions: one mean can hide subgroups inside a category. Example selection defines the prototype.
- Validation: inspect the examples. Measure precision, recall, and abstention. Tune the margin on separate data.
- Claim only: category assignments with similarities and recorded abstentions.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb centroids-classify --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
