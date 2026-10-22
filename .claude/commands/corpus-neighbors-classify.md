---
description: "kNN vote labels with the neighbor evidence listed"
argument-hint: "[--k N] [--chunk-id <id> ...]"
---
# corpus-neighbors-classify — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: labels (method 14).
3. Check the inputs it needs: embeddings, labels.
4. Budget the calibration effort (low) and autonomy (automated_with_audit).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb neighbors-classify [--k N] [--chunk-id <id> ...]
  ```

- Cautions: local label errors travel. Overrepresented examples can dominate nearby votes.
- Validation: review example coverage. Tune the neighbor count and the rejection rules.
- Claim only: category assignments backed by the listed neighbor examples.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb neighbors-classify --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
