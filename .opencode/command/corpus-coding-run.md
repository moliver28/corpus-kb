---
description: Run the deductive coding pass against a codebook version
---
# corpus-coding-run — deductive coding run

Score codable units against a codebook version (three-view scoring with
calibrated thresholds and conformal routing).

```
corpus-kb coding run --codebook-version-id <uuid> [--project-id <uuid>] [--alpha 0.1]
```

- The summary JSON reports explicit / question-dependent / review splits.
- Assignments land after the projection catch-up (run for you).
- All flags: `corpus-kb coding run --help`
