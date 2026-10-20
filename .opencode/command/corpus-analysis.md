---
description: "Analysis program: date backfill + the composed analysis run"
---
# corpus-analysis — the composed analysis run

Date provenance plus the composed corpus analysis report. The run is
dormant until you enable the analysis flags it needs.

- Backfill `documents.document_date` from chunk metadata (idempotent;
  needs `analysis.topics.over_time.enabled: true`):

  ```
  corpus-kb analysis-backfill-dates
  ```

- Run the composed analysis (dates, labels, predictions, trends) and
  write the report:

  ```
  corpus-kb analyze-run [--output <path>]
  ```

- A disabled feature is an honest no-op result, not an error.
- All flags: `corpus-kb analyze-run --help`
