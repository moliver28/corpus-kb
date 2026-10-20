---
description: "Classification layer: few-shot build over a seed set + predictions list"
---
# corpus-classification — few-shot classification over the corpus

Fit a classifier against a seed label set and write calibrated chunk
predictions. Dormant by default: `analysis.classification.enabled:
false` in config.yaml.

- Build the predictions (tier `head` on stored embeddings, or the
  opt-in `setfit` tier):

  ```
  corpus-kb classification-build [--seed-file <path>]
  ```

- List the built predictions (bounded window):

  ```
  corpus-kb classification-list [--limit N]
  ```

- A disabled feature is an honest no-op result, not an error.
- All flags: `corpus-kb classification-build --help`
