---
description: "Near-duplicate candidate pairs at a cosine threshold"
argument-hint: "[--threshold F] [--limit N]"
---
# corpus-duplicates-screen — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: errors (method 29).
3. Check the inputs it needs: raw_text, embeddings.
4. Budget the calibration effort (moderate) and autonomy (automated_with_audit).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb duplicates-screen [--threshold F] [--limit N]
  ```

- Cautions: similar meaning does not make two chunks interchangeable evidence. Merging can erase independent speakers or minority wording.
- Validation: label candidate pairs by hand. Measure pairwise precision and recall at the action threshold.
- Claim only: duplicate candidates at a recorded threshold, for human approval.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb duplicates-screen --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
