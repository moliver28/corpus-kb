"""Ingest queue: durable work orders for the textbook-ingestion program.

``queue.store`` — idempotent enqueue, deterministic reads, the cancel path.
The shared handlers live in ``tools.queue_tools`` (one operation, one
handler, many surfaces); this package holds only the persistence.
"""

from __future__ import annotations

from .store import IngestQueueStore

__all__ = ["IngestQueueStore"]
