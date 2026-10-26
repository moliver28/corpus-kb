"""Stage 3: the additive shadow hierarchy write."""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from ...ingest.hierarchy_settings import HierarchySettings, load_hierarchy_settings
from ...storage.gateway import SqlGateway
from ...utils.models import Chunk, Document

logger = logging.getLogger(__name__)


async def _stage_shadow_hierarchy(
    config: dict[str, Any],
    document: Document,
    text: str,
    chunks: list[Chunk],
    source: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    errors: list[str],
    strategy: str | None = None,
) -> tuple[HierarchySettings, int, int, str | None]:
    """Stage 3: the U48/P6 side-by-side SHADOW hierarchy (additive only).

    The v1 chunks/vectors stay the live search corpus; any shadow failure is
    logged and isolated so it can never break the live ingest. ``strategy``
    (the resolved strategy name) steers the shadow tree's boundary adapter.
    Returns (hierarchy_settings, shadow_chunk_count, shadow_vector_count,
    shadow_note).
    """
    hierarchy_settings = load_hierarchy_settings(config)
    shadow_chunk_count = 0
    shadow_vector_count = 0
    shadow_note: str | None = None
    if hierarchy_settings.enabled:
        try:
            from ..shadow_ingest import write_shadow_hierarchy

            shadow_chunk_count, shadow_vector_count, shadow_note = await write_shadow_hierarchy(
                pool=source,
                tenant_id=tenant_id,
                document_id=document.document_id,
                text=text,
                chunks=chunks,
                config=config,
                settings=hierarchy_settings,
                strategy=strategy,
            )
        except Exception as exc:
            logger.warning("Shadow hierarchy write failed: %s", exc)
            errors.append(f"ShadowWriteError: {exc}")
    return hierarchy_settings, shadow_chunk_count, shadow_vector_count, shadow_note
