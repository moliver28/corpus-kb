---
description: "Labeling layer: build chunk labels, list labels and labeling functions, triage weak labels, gold-set check"
argument-hint: "[--limit N]"
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

- Rank weakly supported labels for human review (advisory; never
  relabels):

  ```
  corpus-kb labeling-triage [--cv-folds 5] [--flag-threshold 0.2]
  ```

- Score the built labels against a small gold dev set file (a JSON
  object that maps chunk ids to label names):

  ```
  corpus-kb labeling-gold-check --gold-file <path>
  ```

- A disabled feature is an honest no-op result, not an error.
- All flags: `corpus-kb labeling-build --help`
