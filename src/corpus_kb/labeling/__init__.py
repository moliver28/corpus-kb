"""Weak-supervision labeling functions over the corpus (WP-2, analysis program).

User-authored labeling functions (regex, lexicon, negation-guarded regex,
metadata predicate) are scored over the tenant's chunks; the
(n_items, n_LFs) matrix feeds the existing Dawid-Skene aggregation
(``coding/aggregation.py``) into probabilistic chunk labels. No Snorkel
dependency. Modules:

- ``protocol``  — the ``LabelingFunction`` record and its ``apply``.
- ``kinds``     — the four built-in LF scorers (regex, lexicon,
  negation-guarded regex, metadata predicate).
- ``lfs``       — the JSON file schema and load-time validation.
- ``engine``    — batched tenant-chunk reads + the scored matrix.
- ``analysis``  — per-LF coverage/overlap/conflict/polarity + the F6
  correlation guard (pairwise agreement warnings).
- ``ppi_check`` — validation against a small gold dev set through
  ``coding/ppi.py``.
- ``writer``    — tenant-scoped Postgres reads/writes (migration 028).
"""

from __future__ import annotations
