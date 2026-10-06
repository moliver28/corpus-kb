"""Deterministic identifiers shared by the projection read paths.

The pre-spike DocumentsProjection wrote ``chunk_id = UUID(int=0)`` for every
chunk and EmbedChunksProjection read a ``chunk_ids`` key the event never
emitted (todo-11 STEP 0 spike, item (c) — both defects fixed here). Chunks
are derived data keyed by (tenant, document, position), so their stable id is
uuid5 over the document id + position: identical on replay, which is what
makes projection rebuilds idempotent.
"""

from __future__ import annotations

import uuid
from uuid import UUID


def deterministic_chunk_id(doc_id: UUID | str, index: int) -> UUID:
    """Stable chunk id for (document, position), identical across replays."""
    try:
        namespace = doc_id if isinstance(doc_id, UUID) else UUID(str(doc_id))
    except ValueError:
        namespace = uuid.NAMESPACE_URL
    return uuid.uuid5(namespace, f"chunk:{index}")


def deterministic_event_id(originator_id: UUID | str, version: int) -> UUID:
    """Stable stand-in event id for checkpoint/DLQ rows keyed by UUID."""
    try:
        namespace = originator_id if isinstance(originator_id, UUID) else UUID(str(originator_id))
    except ValueError:
        namespace = uuid.NAMESPACE_URL
    return uuid.uuid5(namespace, f"event:{version}")
