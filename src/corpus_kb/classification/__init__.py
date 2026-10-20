"""Few-shot classification over stored embeddings (WP-3, analysis program).

A seed label set (a JSON file of ``chunk_id``/``label`` pairs) fits a
classifier over the tenant's stored chunk vectors, scores every stored
chunk, calibrates the confidence, and routes low confidence to the
review flag. Two tiers:

- ``head`` (default): sklearn LogisticRegression on the stored vectors.
  CPU-deterministic, minutes, zero new inference (``tier_a.py``).
- ``setfit`` (opt-in ``[setfit]`` extra): frozen-body SetFit with the
  bge-small-en-v1.5 encoder (512-token context), lazy-imported
  (``tier_b.py``).

Modules:

- ``engine.py`` — the stored-vector reader (CONSUMES, never embeds).
- ``seeds.py`` — seed-file parse, validation, and resolution.
- ``tier_a.py`` — the logistic head (the shared head math lives here).
- ``tier_b.py`` — the SetFit tier (lazy import, CPU-pinned, seeded).
- ``calibrate.py`` — Platt scaling on out-of-fold decision scores.
- ``store.py`` — migration-029 reads and the wholesale-replace write.
- ``predict.py`` — the one build orchestration both tiers share.
"""

from __future__ import annotations
