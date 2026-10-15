"""Offline topic modeling over stored chunk embeddings (Wave 4).

The pipeline consumes the vectors ingest already wrote
(``chunks_vectors.embedding``, halfvec(2560)); it never re-embeds.
Modules:

- ``settings``   — typed ``topics.*`` reads (load-time validation).
- ``engine``     — the BERTopic fit (lazy import behind the ``topics``
  extra; deterministic UMAP pin recorded in every result).
- ``reduction``  — LLM topic labels/merges, fixture-first, fail-open.
- ``writer``     — tenant-scoped Postgres writes (migration 026).
"""

from __future__ import annotations
