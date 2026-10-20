# Analysis

The analysis layer reads the corpus offline and writes structured
results. One command, `analyze-run`, composes four features into one
report. The default config ships every feature dormant.

## How it works

`analyze-run` runs four sections in a fixed order:

1. `dates-coverage` — refreshes `documents.document_date` (the
   backfill) and reports how many documents carry a date.
2. `labeling` — scores your labeling functions over the chunks and
   writes probabilistic labels (Dawid-Skene aggregation).
3. `classification` — reports the seed-set state. The run never
   invents seeds. With no stored set the section is `skipped`. With
   one, the section reports the stored totals. A re-fit stays
   with `classification-build` and your seed file.
4. `trends` — refreshes the dates first, then counts each topic per
   date bin over the dated chunks.

Each section calls that feature's one handler. No section
re-implements a pipeline. Each section answers on its own: one failed
section never fails the run. The report lists every section status.

With `--output`, the run writes a JSON report and a rendered markdown
file beside it. The output path holds the JSON; the markdown sits next
to it with the `.md` suffix. The JSON is the snapshot. The markdown is
for reading.

## Requirements

1. Migrations 027, 028, and 029 (they ship with `corpus-kb setup`).
2. The flags you need set to true in `config.yaml` (see Config).
3. Labeling needs `analysis.labeling.lfs_file` (the LF file).
4. Classification needs a stored seed label set. Build one first with
   `classification-build` and a seed file.
5. Trends need the topics extra: `pip install 'corpus-kb[topics]'`.

## Commands

```bash
corpus-kb analyze-run                    # the composed run + report
corpus-kb analyze-run --output report.json
corpus-kb analysis-backfill-dates        # refresh document dates only
corpus-kb labeling-build                 # score LFs, write labels
corpus-kb labeling-list                  # the built labels
corpus-kb labeling-lfs                   # the registered LFs + stats
corpus-kb classification-build --seed-file seeds.json
corpus-kb classification-list            # the built predictions
corpus-kb topics-trends                  # trends only (docs/TOPICS.md)
```

Every command also runs as an MCP tool, an HTTP route, and a socket
method. Exit codes: see docs/OPERATIONS.md.

## Determinism

The section order is fixed. Report keys are sorted. The artifact holds
no wall-clock timestamps; each section records its build params
instead. Same corpus, same config, byte-identical report files.

## Config

```yaml
analysis:
  labeling:
    enabled: false         # the dormant default
    min_signals: 3         # LFs that must fire before a label is accepted
    lfs_file: null         # path to the LF definitions (JSON)
  classification:
    enabled: false         # the dormant default
    tier: head             # head (stored vectors) or setfit (the [setfit] extra)
    min_labels_per_class: 8
  topics:
    over_time:
      enabled: false       # gates trends AND the date backfill
      bins: auto           # "auto" = 10 bins, or a positive int
      undated: drop        # drop (count them) or report (list up to 20 ids)
    max_embeddings: null   # null = the engine default (20000)
```

These keys have no environment overrides. Edit `config.yaml`.

## Input files

The LF file (`analysis.labeling.lfs_file`) holds one JSON object:

```json
{
  "classes": ["relevant", "noise"],
  "lfs": [
    {"name": "mentions-sla", "kind": "regex", "label": "relevant",
     "config": {"pattern": "\\bSLA\\b"}}
  ]
}
```

Four kinds exist: `regex`, `lexicon`, `negation_regex`, and
`metadata`. A bad file fails at load, with the fix in the message.

The seed file holds a JSON list. Each row pairs one stored chunk with
one class name:

```json
[
  {"chunk_id": "00000000-0000-0000-0000-000000000001", "label": "alpha"}
]
```

Every class needs at least `min_labels_per_class` seeds.

## Statuses

| Status | Meaning |
|---|---|
| `success` | The section ran and wrote its result. |
| `disabled` | The feature flag is off. Labeling also without an LF file. |
| `skipped` | Classification has no seed label set yet. |
| `failed` | The handler raised. The reason carries the message. |
| `unavailable` | The migration for this feature has not run. |
| `insufficient_data` | The corpus is too small for this feature. |
| `insufficient_labels` | The seed set is missing, thin, or unreadable. |

The run status is `success` when at least one section succeeded. When
every flag is off, the run answers `disabled` and writes nothing.

## Storage

Migration 027 adds `documents.document_date`. Migration 028 adds the
labeling tables (`label_functions`, `chunk_labels`). Migration 029
adds the classification tables (`label_sets`, `chunk_predictions`).

Each build replaces its tenant's derived rows. The seed sets and the
LF registry survive rebuilds. Delete the derived rows and nothing
breaks; one command rebuilds them.
