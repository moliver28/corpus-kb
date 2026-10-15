# Topics

The topic layer groups ingested chunks into themes. It runs offline,
after ingest, on your command. The default config ships it dormant.

## How it works

`topics-build` reads the vectors ingest already wrote
(`chunks_vectors.embedding`). It never re-embeds anything. The BERTopic
pipeline clusters those vectors and labels each cluster with its
strongest terms (c-TF-IDF). Outlier chunks stay unassigned.

When `topics.llm_reduction` is on, a local Ollama model writes a short
label per topic. It may also merge near-duplicate topics. This step
never fails the build. A down model keeps the c-TF-IDF labels. The
result records the note.

## Requirements

1. Migration 026 (ships with `corpus-kb setup`).
2. The optional extra: `pip install 'corpus-kb[topics]'`.
3. `topics.enabled: true` in `config.yaml`.
4. More embedded chunks than `topics.min_topic_size` (default 10).

## Commands

```bash
corpus-kb topics-build          # cluster + label + write
corpus-kb topics-list           # the built topics, largest first
```

Both commands also run as MCP tools (`topics_build`, `topics_list`),
HTTP routes (`POST /api/topics/build`, `GET /api/topics`), and socket
methods. Exit codes: see docs/OPERATIONS.md.

## Determinism

The UMAP random state is pinned at 42 (the repo-wide research pin).
Every model parameter lands in the build result and in the `topics`
table (`params` jsonb). Same corpus, same build.

## Config

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

## Storage

Migration 026 adds two tenant-scoped tables:

- `topics` — one row per topic: label, reduced label, size, keywords,
  and the parameter record.
- `chunk_topics` — one row per assigned chunk, with the membership
  probability.

Every build replaces the tenant's rows. The tables are derived data:
delete them and nothing breaks; rebuild with one command.
