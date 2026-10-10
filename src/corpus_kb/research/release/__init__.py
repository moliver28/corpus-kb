"""Codebook release core (U1-U6, U25 data, U43/U44/U48 evidence) — P1.

The lock layer: releases freeze a CodebookVersion behind a canonical,
SHA-256-hashed manifest, gate evidence, and human approval. The aggregate
(``domain/codebook.py``) owns the event chain; the projector in
``projections/research`` owns the four read tables (migration 019); this
package owns the manifest, profiles, gate framework, determinism proof,
consensus, and the lifecycle service.
"""

from __future__ import annotations
