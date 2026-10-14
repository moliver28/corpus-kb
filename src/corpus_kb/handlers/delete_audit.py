"""The Deleted audit event for document deletes (zero-drift Wave 3A).

ONE delete, ONE audit: the shared tool (``tools.ingest_tools.delete_document``)
owns the real row delete; :func:`attach_delete_event` is the event-store
wrapper the command handler attaches after it — the same additive pattern
as the ingest audit (``command_handler._attach_events``). Best effort: the
row delete already happened, and a failed audit write never fails the
delete. A document with no event-store history (tool-path ingests) deletes
with no audit event, so unknown ids never raise here.
"""

from __future__ import annotations

import logging
from uuid import UUID

from corpus_kb.domain.aggregates import Document
from corpus_kb.domain.application import get_app

logger = logging.getLogger(__name__)


def attach_delete_event(result: dict[str, object], doc_id: str) -> dict[str, object]:
    """Attach the Deleted audit event to a SUCCESSFUL delete envelope.

    Adds the additive keys ``aggregate_id``/``aggregate_version`` when the
    event saved, or ``event_sourcing_error`` when the audit failed after the
    successful delete. Non-success envelopes pass through unchanged.
    """
    if result.get("status") != "success":
        return result

    try:
        app = get_app()
        doc_events = list(app.repository.get(UUID(doc_id)))
        if doc_events:
            doc = Document.reconstruct(UUID(doc_id), doc_events)
            doc.delete()
            app.save(doc)
            result["aggregate_id"] = doc_id
            result["aggregate_version"] = doc.version
    except Exception as exc:
        logger.warning("Delete audit failed (deletion succeeded): %s", exc)
        result["event_sourcing_error"] = str(exc)

    return result
