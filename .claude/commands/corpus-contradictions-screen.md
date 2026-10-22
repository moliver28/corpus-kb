---
description: "NLI contradiction candidates for human adjudication"
argument-hint: "[--candidate-k N] [--limit N] [--model <tag>]"
---
# corpus-contradictions-screen — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: errors (method 28).
3. Check the inputs it needs: raw_text, llm, timestamps.
4. Budget the calibration effort (moderate) and autonomy (human_in_loop).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb contradictions-screen [--candidate-k N] [--limit N] [--model <tag>]
  ```

- Cautions: different times or conditions can make an apparent contradiction compatible. Candidate selection favors disagreement.
- Validation: test negation, entity identity, time, and conditions. Adjudicate the pairs by hand.
- Claim only: candidate contradictions for human adjudication.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb contradictions-screen --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
