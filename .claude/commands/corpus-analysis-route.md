---
description: "Route a plain-language question to an analysis method (the front door)"
argument-hint: "<question> [--method-id <id>]"
---
# corpus-analysis-route — question front door

Route one plain-language question to the right analysis method.

- Ask the question; read the method, the one-line why, and the run hint:

  ```
  corpus-kb analysis-route "<question>"
  ```

- An ambiguous answer lists readings. Re-run with your pick:

  ```
  corpus-kb analysis-route "<question>" --method-id <id>
  ```

- The status says resolved, ambiguous, or degraded. Degraded means no
  embedder ran; the phrase rules still answered, and the trace says
  which mode ran.
- The trace records the mode, the threshold, the margin, and the scores.
- Exit codes: 0 answered, 1 failed, 3 unavailable.
- All flags: `corpus-kb analysis-route --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
