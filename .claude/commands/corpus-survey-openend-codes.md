---
description: "Code open-ended survey answers per question with counts"
argument-hint: "[--chunk-id <id> ...] [--min-cluster-size N]"
---
# corpus-survey-openend-codes — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: labels (method 31).
3. Check the inputs it needs: raw_text, embeddings.
4. Budget the calibration effort (moderate) and autonomy (human_in_loop).
5. Read the cautions. Run. Then do the validation step.

## Progression (the register's ladder)

- Difficulty: `novice`.
- Teaches: `clustering`, `qualitative_coding`.
- Run first: method 24, ask or a survey ingest, so survey answers exist as chunks.

## Run

  ```
  corpus-kb survey-openend-codes [--chunk-id <id> ...] [--min-cluster-size N]
  ```

- Cautions: cluster labels come from exemplar answers. Small but important groups can hide in the noise. This is not human coding.
- Validation: check a sample of coded answers by hand. Compare the labels against the raw answers for mixed or sarcastic cases.
- Claim only: per-question code labels, counts, and exemplar answers.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb survey-openend-codes --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
