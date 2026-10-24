"""Shared success-envelope key tuples and additive-key sets for the catalogue.

Guaranteed-on-success keys only in the tuples: conditional keys (embed_note,
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
    "statements",
    "profile",
)

# Shared search family (QueryHandler.handle_search_with_meta serialized by
# handlers/query_integrity.py envelope_to_payload). Storage-portability §6:
# every engine states which index machinery served the search. The
# conditional ``vector_index_note`` / ``lexical_index_note`` keys are
# deliberately absent (the embedding_mode/embedding_note contract shape).
SEARCH_ENVELOPE = (
    "status",
    "result",
    "degraded_excluded",
    "vector_index_mode",
    "lexical_index_mode",
)

# Declared additive HTTP/socket wrapper over the shared envelope: the
# command handler attaches the audit aggregate to successful ingests and
# deletes. ``event_sourcing_error`` appears only when the audit write
# failed after a successful pipeline.
AGGREGATE_EXTRA = frozenset({"aggregate_id", "aggregate_version", "event_sourcing_error"})
