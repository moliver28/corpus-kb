---
description: "Topic layer: offline BERTopic build + list over stored embeddings"
argument-hint: "[--limit N]"
---
# corpus-topics — the topic layer (Wave 4)

Offline BERTopic over the chunk embeddings ingest already wrote. The
feature is dormant by default: `topics.enabled: false` in config.yaml.

- Build topics (consumes `chunks_vectors.embedding`; never re-embeds):

  ```
  corpus-kb topics-build
  ```

- List the built topics, largest first:

  ```
  corpus-kb topics-list [--limit N]
  ```

- Topic trends over document dates (analysis program; dormant by
  default, needs `analysis.topics.over_time.enabled: true`):

  ```
  corpus-kb topics-trends [--bins N]
  ```

- A disabled feature is an honest no-op result, not an error. A missing
  extra exits 3 with the remedy: `pip install 'corpus-kb[topics]'`.
- All flags: `corpus-kb topics-build --help`
