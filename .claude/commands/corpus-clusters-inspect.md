---
description: "Cluster structure report: sizes, noise, exemplars, reduction"
argument-hint: "[--min-cluster-size N] [--sample-cap N]"
---
# corpus-clusters-inspect — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: structures (method 12).
3. Check the inputs it needs: raw_text, embeddings.
4. Budget the calibration effort (moderate) and autonomy (human_in_loop).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb clusters-inspect [--min-cluster-size N] [--sample-cap N]
  ```

- Cautions: a stable-looking group can reflect topic, style, speaker, or document format. The params record names the reduction that ran.
- Validation: compare representations and grouping settings. Review boundary cases by hand.
- Claim only: exploratory groups with examples and distances.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb clusters-inspect --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
