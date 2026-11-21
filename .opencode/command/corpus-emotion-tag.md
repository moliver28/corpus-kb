---
description: "Tag chunks with the fixed eight-emotion set"
---
# corpus-emotion-tag — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: labels (method 33).
3. Check the inputs it needs: raw_text, llm.
4. Budget the calibration effort (moderate) and autonomy (automated_with_audit).
5. Read the cautions. Run. Then do the validation step.

## Progression (the register's ladder)

- Difficulty: `intermediate`.
- Teaches: `classification`, `sentiment_analysis`.

## Run

  ```
  corpus-kb emotion-tag [--chunk-id <id> ...] [--limit N] [--model <tag>]
  ```

- Cautions: zero-shot entailment scores are not established probabilities. Mixed feelings, irony, and statement wording can flip the judgments.
- Validation: test the statements against paraphrased, negated, and mixed-affect records. Check a sample of tags by hand.
- Claim only: zero-shot emotion tags under the fixed eight-label set.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb emotion-tag --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
