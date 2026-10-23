---
description: "Rank the register's methods for a stated task (the axis walk)"
argument-hint: "<task> [--output-family <fam>] [--input <kind>] [--max-tier <tier>]"
---
# corpus-analysis-select-method — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: any (you name it).
3. Check the inputs it needs: none (the selector reads the register, not the corpus).
4. Budget the calibration effort (none) and autonomy (automated_with_audit).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb analysis-select-method "<task>" [--output-family <family>] [--input <kind>] [--max-complexity-tier <tier>]
  ```

- Cautions: the rank is registry order, not evidence quality. The rubric cannot see your question's meaning.
- Validation: read the top rows' validation_required and permitted_claims before you pick one.
- Claim only: a ranked shortlist with the register version recorded.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb analysis-select-method --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
