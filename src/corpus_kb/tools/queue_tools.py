"""The ingest-queue op handlers (textbook-ingestion program, Wave 1).

Three shared functions, one per catalogue row: ``queue_enqueue``
(idempotent by content hash), ``queue_status`` (one row or the active
list), ``queue_cancel`` (pending/running only). This module never writes
SQL — persistence lives in ``corpus_kb.queue.store``. The MCP, HTTP,
socket, and CLI surfaces are thin adapters over these functions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import asyncpg

from ..errors import ConflictError, NotFoundError, ValidationError
from ..utils.config_sections import mapping_section
from .ingest_common import DEFAULT_TENANT_ID
from .queue_payload import queue_entry_payload

__all__ = ["content_hash_for", "queue_cancel", "queue_enqueue", "queue_status"]

_BAD_PATH_REASON = "path is required."
_BAD_PATH_REMEDY = "Pass the file path to queue, then try again."
_BAD_CONFIG_REASON = "strategy_config must be a JSON object."
_BAD_CONFIG_REMEDY = "Pass strategy_config as an object of strategy keys, or omit it."

DEFAULT_MAX_ATTEMPTS = 3


def content_hash_for(
    path: str, profile: str | None, strategy_config: Mapping[str, object] | None
) -> str:
    """The idempotency key: sha256 of abs path + profile + canonical config.

    The config renders as canonical JSON (sorted keys, tight separators),
    so two enqueues of the same file with the same settings collide on
    purpose, and a different strategy config never does.
    """
    canonical = json.dumps(dict(strategy_config or {}), sort_keys=True, separators=(",", ":"))
    raw = f"{Path(path).resolve()}|{profile or ''}|{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _max_attempts_from(config: Mapping[str, object] | None) -> int:
    """The queue.max_attempts knob; junk or absent answers the default."""
    raw = mapping_section(config, "queue").get("max_attempts")
    if isinstance(raw, bool) or raw is None:
        return DEFAULT_MAX_ATTEMPTS
    if isinstance(raw, int):
        return raw if raw >= 1 else DEFAULT_MAX_ATTEMPTS
    try:
        value = int(str(raw))
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS
    return value if value >= 1 else DEFAULT_MAX_ATTEMPTS


def _strategy_config(raw: object) -> dict[str, object]:
    """Validate and copy the caller's strategy-config mapping."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValidationError(_BAD_CONFIG_REASON, remediation=_BAD_CONFIG_REMEDY)
    return dict(raw)


async def queue_enqueue(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    path: str | None = None,
    title: str | None = None,
    profile: str | None = None,
    strategy_config: dict[str, object] | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """Queue one file for ingestion; idempotent per tenant + content hash.

    Returns ``{status, queue_id, content_hash, deduplicated, state}``.
    A repeat enqueue of the same file, profile, and strategy config
    answers the existing pending/running row with ``deduplicated: true``.
    """
    from ..queue import IngestQueueStore

    clean_path = (path or "").strip()
    if not clean_path:
        raise ValidationError(_BAD_PATH_REASON, remediation=_BAD_PATH_REMEDY)
    clean_config = _strategy_config(strategy_config)
    content_hash = content_hash_for(clean_path, profile, clean_config)
    store = IngestQueueStore(pg_pool, tenant_id)
    row, deduplicated = await store.enqueue(
        path=str(Path(clean_path).resolve()),
        title=(title.strip() or None) if isinstance(title, str) else None,
        project_id=project_id,
        profile=profile,
        strategy_config=clean_config,
        max_attempts=_max_attempts_from(config),
        content_hash=content_hash,
    )
    return {
        "status": "queued",
        "queue_id": row.get("id"),
        "content_hash": content_hash,
        "deduplicated": deduplicated,
        "state": row.get("status"),
    }


async def queue_status(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    queue_id: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """One queue row, or the pending + running list, oldest queued first.

    Returns ``{status, entries, count}``. ``entries`` carries the row
    payloads; the progress keys (``pages_done``, ``pages_total``,
    ``chunks``) are nullable and fill in once a worker runs the row.
    """
    from ..queue import IngestQueueStore

    if limit < 1:
        raise ValidationError("limit must be a positive integer.")
    store = IngestQueueStore(pg_pool, tenant_id)
    if queue_id:
        row = await store.get(queue_id)
        if row is None:
            raise NotFoundError(
                f"Queue entry {queue_id} not found for this tenant.",
                remediation="Run queue-status without a queue id to list active entries.",
            )
        entries: list[dict[str, object]] = [queue_entry_payload(row)]
    else:
        entries = [queue_entry_payload(row) for row in await store.list_active(limit)]
    return {"status": "success", "entries": entries, "count": len(entries)}


async def queue_cancel(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    queue_id: str | None = None,
) -> dict[str, object]:
    """Cancel one pending or running queue row.

    Returns ``{status, queue_id, state}``. A done, failed, or cancelled
    row cannot be cancelled: that is a conflict, not a success shape.
    """
    from ..queue import IngestQueueStore

    clean_id = (queue_id or "").strip()
    if not clean_id:
        raise ValidationError(
            "queue_id is required.",
            remediation="Pass the queue entry id, then try again.",
        )
    store = IngestQueueStore(pg_pool, tenant_id)
    row = await store.cancel(clean_id)
    if row is None:
        existing = await store.get(clean_id)
        if existing is None:
            raise NotFoundError(
                f"Queue entry {clean_id} not found for this tenant.",
                remediation="Run queue-status to list active entries.",
            )
        state = existing.get("status") or "in a terminal state"
        raise ConflictError(
            f"Queue entry {clean_id} is {state} and cannot be cancelled.",
            remediation="Only pending or running entries can be cancelled.",
        )
    return {"status": "success", "queue_id": clean_id, "state": row.get("status")}
