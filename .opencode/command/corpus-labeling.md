---
description: "Labeling layer: build chunk labels, list labels and labeling functions"
---
# corpus-labeling — the weak-supervision labeling layer

Score user-authored labeling functions over the corpus and aggregate
them into probabilistic chunk labels. Dormant by default:
`analysis.labeling.enabled: false` in config.yaml.

- Build the chunk labels from the registered labeling functions:

  ```
  corpus-kb labeling-build
  ```

- List the built chunk labels and the registered functions:

  ```
  corpus-kb labeling-list [--limit N]
  corpus-kb labeling-lfs [--limit N]
  ```

- A disabled feature is an honest no-op result, not an error.
- All flags: `corpus-kb labeling-build --help`
