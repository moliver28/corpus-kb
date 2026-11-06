"""Project raw-content storage for the pipeline (Track C3).

When a run carries a ``project_id`` and that project has a provisioned
raw table (the ``project_tables`` registry is the lookup), the pipeline
writes the source content into ``raw_<slug>`` through the ONE factory
writer (``storage/project_tables.store_raw_content``) and reports
``raw_table`` / ``raw_stored`` in the ingest envelope. No registry row,
no raw write — provisioning is the explicit opt-in.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import asyncpg

from corpus_kb.storage.gateway import SqlGateway

logger = logging.getLogger(__name__)

__all__ = ["attach_project_raw"]

_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".vtt": "text/vtt",
    ".srt": "text/plain",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


async def _raw_table_for(
    gw: asyncpg.Pool | SqlGateway, tenant_id: str, project_id: str
) -> str | None:
    """The table name the registry pins to this project, or None."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(gw, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT table_name FROM project_tables WHERE tenant_id = $1 AND project_id = $2",
            tenant_id,
            project_id,
        )
    if row is None:
        return None
    name = row["table_name"]
    return str(name) if name else None


async def attach_project_raw(
    result: dict[str, Any],
    text: str,
    path: str,
    is_file: bool,
    pg_pool: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    project_id: str | None,
) -> None:
    """Store the raw source content for one project run; set envelope keys.

    Best effort at the lookup: a missing registry row leaves the envelope
    untouched (no project, no raw write). A write failure records a stage
    error so the run lands ``partial`` — raw loss is never silent.
    """
    if not project_id:
        return
    try:
        table = await _raw_table_for(pg_pool, tenant_id, project_id)
    except Exception as exc:
        logger.warning("Could not look up the project raw table: %s", exc)
        return
    if table is None:
        return
    raw_bytes: bytes | None = None
    if is_file:
        try:
            raw_bytes = Path(path).read_bytes()
        except OSError as exc:
            result.setdefault("errors", []).append(f"ProjectRawError: {exc}")
            return
    slug = table[len("raw_") :].replace("_", "-")
    from corpus_kb.storage.project_tables import store_raw_content

    try:
        source_hash = await store_raw_content(
            pg_pool,
            tenant_id,
            project_id,
            slug,
            source_path=path,
            raw_bytes=raw_bytes,
            raw_text=None if raw_bytes is not None else text,
            content_type=_MIME_BY_SUFFIX.get(Path(path).suffix.lower()),
        )
    except Exception as exc:
        result.setdefault("errors", []).append(f"ProjectRawError: {exc}")
        return
    result["raw_table"] = table
    result["raw_stored"] = {
        "bytes": len(raw_bytes) if raw_bytes is not None else 0,
        "text": 0 if raw_bytes is not None else len(text),
        "sha256": source_hash,
    }
