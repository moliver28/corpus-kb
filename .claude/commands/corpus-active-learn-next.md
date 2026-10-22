---
description: "Next human-labeling batch ranked from stored predictions"
argument-hint: "[--strategy margin|entropy|random_incumbent] [--batch N]"
---
# corpus-active-learn-next — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: recommendations (method 20).
3. Check the inputs it needs: labels, predictions.
4. Budget the calibration effort (high) and autonomy (human_in_loop).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb active-learn-next [--strategy margin|entropy|random_incumbent] [--batch N]
  ```

- Cautions: uncertainty sampling can chase difficult or noisy cases instead of representative ones. The random_incumbent strategy guards against that bias.
- Validation: assess the improvement per annotation effort. Compare against random sampling.
- Claim only: annotation-order recommendations with the strategy recorded.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb active-learn-next --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
