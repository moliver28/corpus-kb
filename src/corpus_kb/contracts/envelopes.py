"""Shared success-envelope key tuples for the operation catalogue.

Guaranteed-on-success keys only: conditional keys (embed_note,
embedding_note, empty_reason, shadow_*) are deliberately absent, so the
parity gate's subset check holds for every outcome the surfaces return.
"""

from __future__ import annotations

# Shared ingest pipeline (tools/ingest_common.py run_pipeline).
INGEST_ENVELOPE = (
    "status",
    "document_id",
    "path",
    "source_type",
    "size_bytes",
    "chunk_count",
    "entity_count",
    "relation_count",
    "pg_chunk_count",
    "pg_vector_count",
    "degraded",
    "extractor_id",
    "extraction",
    "entities",
    "errors",
    "run_id",
    "embedding_mode",
)
