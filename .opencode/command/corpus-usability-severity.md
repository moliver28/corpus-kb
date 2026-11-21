---
description: "Score findings with the fixed 0-4 severity rubric"
---
# corpus-usability-severity — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: scores (method 32).
3. Check the inputs it needs: raw_text.
4. Budget the calibration effort (low) and autonomy (automated_with_audit).
5. Read the cautions. Run. Then do the validation step.

## Progression (the register's ladder)

- Difficulty: `novice`.
- Teaches: `keyword_rules`.
- Run first: method 22, analyze_run summaries, so findings exist as chunks.

## Run

  ```
  corpus-kb usability-severity [--chunk-id <id> ...] [--limit N]
  ```

- Cautions: anchor words miss indirect wording, and the score reads wording, not measured impact. Unmatched rows score 0 and route to review.
- Validation: read a sample of scored findings against the severity anchors. Recalibrate the anchor word lists per study.
- Claim only: rubric-based screening scores under the fixed 0 to 4 anchors.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb usability-severity --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
