---
description: "Preset lexicon sentiment screen with the honest evidence note"
argument-hint: "[--chunk-id <id> ...] [--limit N]"
---
# corpus-sentiment-screen — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: scores (method 26) and labels (method 27).
3. Check the inputs it needs: raw_text (method 27 also names labels).
4. Budget the calibration effort (moderate to high) and autonomy (automated_with_audit to human_in_loop).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb sentiment-screen [--chunk-id <id> ...] [--limit N]
  ```

- Cautions: this is a preset lexicon vote, not a trained sentiment model. Expression style can be mistaken for an emotional state. Ties route to review.
- Validation: define the construct. Label and test each category, including neutral and uncertain cases. Check a sample by hand.
- Claim only: screening flags for evaluative language under the defined construct.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb sentiment-screen --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
