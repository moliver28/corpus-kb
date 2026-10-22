---
description: "Zero-shot labels via entailment judgments"
---
# corpus-nli-classify — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: labels (method 18).
3. Check the inputs it needs: raw_text, llm (an Ollama NLI model).
4. Budget the calibration effort (moderate) and autonomy (automated_with_audit).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb nli-classify --label <label> ... [--chunk-id <id> ...] [--model <tag>]
  ```

- Cautions: wording, lexical overlap, and syntax can swing the support judgment. Label descriptions shape the results.
- Validation: test paraphrased labels, negation, and overlapping labels. Tune thresholds on labeled data.
- Claim only: pilot label proposals with support scores.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb nli-classify --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
