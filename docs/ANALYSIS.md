# Analysis

The analysis layer reads the corpus offline and writes structured
results. One command, `analyze-run`, composes four features into one
report. The default config ships every feature dormant.

## Contents

- [Contents](#contents)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Commands](#commands)
- [Determinism](#determinism)
- [Config](#config)
- [Input files](#input-files)
- [Statuses](#statuses)
- [Storage](#storage)
- [Topics](#topics)
- [Method register and selector](#method-register-and-selector)

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
corpus-kb topics-trends                  # trends only (see Topics)
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

## Topics

The topic layer groups ingested chunks into themes. It runs offline,
after ingest, on your command. The default config ships it dormant.

| Item | Value |
|---|---|
| Command | `corpus-kb topics-build`, `topics-list`, `topics-trends` |
| Migration | 026 (ships with `corpus-kb setup`) |
| Extra | `pip install 'corpus-kb[topics]'` |
| Gate | `topics.enabled: true` |
| Minimum | More embedded chunks than `topics.min_topic_size` (default 10) |

### How topics work

`topics-build` reads the vectors ingest already wrote
(`chunks_vectors.embedding`). It never re-embeds anything. The BERTopic
pipeline clusters those vectors and labels each cluster with its
strongest terms (c-TF-IDF). Outlier chunks stay unassigned.

When `topics.llm_reduction` is on, a local Ollama model writes a short
label per topic. It may also merge near-duplicate topics. This step
never fails the build. A down model keeps the c-TF-IDF labels. The
result records the note.

### Topic requirements

1. Migration 026 (ships with `corpus-kb setup`).
2. The optional extra: `pip install 'corpus-kb[topics]'`.
3. `topics.enabled: true` in `config.yaml`.
4. More embedded chunks than `topics.min_topic_size` (default 10).

### Topic commands

```bash
corpus-kb topics-build          # cluster + label + write
corpus-kb topics-list           # the built topics, largest first
```

Both commands also run as MCP tools (`topics_build`, `topics_list`),
HTTP routes (`POST /api/topics/build`, `GET /api/topics`), and socket
methods. Exit codes: see docs/OPERATIONS.md.

### Topic determinism

The UMAP random state is pinned at 42 (the repo-wide research pin).
Every model parameter lands in the build result and in the `topics`
table (`params` jsonb). Same corpus, same build.

### Topic config

```yaml
topics:
  enabled: false          # the dormant default
  method: bertopic        # only bertopic exists; unknown values refuse to load
  llm_reduction: true     # LLM labels (and merges) on top of c-TF-IDF
  min_topic_size: auto    # "auto" = the BERTopic default (10), or a positive int
  llm: qwen3:4b           # the reduction model
  llm_base_url: http://localhost:11434
  fixture_dir: tests/fixtures/topics_recorded
  live_fallback: false    # recorded answers only, unless you opt in
```

`min_topic_size` sets the smallest cluster BERTopic may form. Raise it
for broad themes; lower it for fine-grained ones.

### Topic storage

Migration 026 adds two tenant-scoped tables:

- `topics` — one row per topic: label, reduced label, size, keywords,
  and the parameter record.
- `chunk_topics` — one row per assigned chunk, with the membership
  probability.

Every build replaces the tenant's rows. The tables are derived data:
delete them and nothing breaks; rebuild with one command.

### Trends over time

`topics-trends` adds dates. It reuses the fit path (it never
re-embeds) and counts each topic per document-date bin over the dated
chunks. Undated chunks follow `analysis.topics.over_time.undated`:
`drop` (the default) skips them and reports the count; `report` also
lists up to 20 chunk ids.

Rebuild contract: run `topics-trends` against a fresh topics state
after a big ingest. The trends run refreshes
`documents.document_date` first. The composed `analyze-run` report
does this for you; its sections and config live in the Config section
above.

## Method register and selector

The analysis method register lists 30 NLP analysis methods. Each row
carries the selection axes from the research source. These are the
output family, the required inputs, the calibration effort, the
autonomy, the complexity tier, and the priority. Each row also carries
the guidance axes. These are the robustness and bias cautions, the
workload, and the usage guidance. They also name the required
validation and the claims the method permits.

`analysis-select-method` ranks methods for a stated task. It runs no
model. The rubric is fixed. Match the output family, then check the
inputs you have. Then prefer the lower complexity tier, then the lower
calibration effort. Then the recorded priority, then the id. Each
recommendation names the rank reason, the missing inputs, the required
validation, and the permitted claims.

The register lives at `config/analysis_methods.yaml`. The generated
table in `docs/ANALYSIS-METHODS.md` is built from it (`poe gen`). To
add a method, add a row. To wire an implementation, follow the
op-addition recipe in the zero-drift program, then extend the row's
status and op fields.
