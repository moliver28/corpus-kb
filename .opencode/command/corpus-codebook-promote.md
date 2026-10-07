---
description: Promote one proposed code into a new codebook version (human gate)
---
# corpus-codebook-promote — promote a proposed code (HUMAN GATE)

Promotion is never automated: review the proposal, then mint the next
codebook version yourself.

```
corpus-kb codebook promote --proposed-id <int> --name "<code name>" \
  --definition "<definition>" [--inclusion ...] [--exclusion ...]
```

- A duplicate-gate block returns exit code 3 with a merge suggestion.
- All flags: `corpus-kb codebook promote --help`
