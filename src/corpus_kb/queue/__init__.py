"""Ingest queue: durable work orders for the textbook-ingestion program.

``queue.store`` — idempotent enqueue, deterministic reads, the cancel
path, and the Wave 2C worker ops (claim, reclaim, heartbeat, finish,
fail). ``queue.worker`` — the drain loop. ``queue.sql`` — every queue
statement. The shared handlers live in ``tools.queue_tools`` (one
operation, one handler, many surfaces).
"""

from __future__ import annotations

from .store import IngestQueueStore
from .worker import QueueWorker
from .worker_support import StopFlag, WorkerIdentity, install_stop_signals

__all__ = [
    "IngestQueueStore",
    "QueueWorker",
    "StopFlag",
    "WorkerIdentity",
    "install_stop_signals",
]
