"""Project projector — the research_projects registry read model (todo-11 (c)).

Projects are a READ MODEL derived from events: Document.Ingested (and the
coding surfaces that carry project context) upsert (project_id, name). No
dedicated Project aggregate exists in the plan; the projector maintains the
registry from the events that reference projects.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections.research._common import event_payload, require_tenant
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)


class ProjectProjector:
    """Maintains research_projects from events that reference a project."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def on_ingested(self, notification: Any) -> None:
        payload = event_payload(notification)
        tenant_id = require_tenant(payload, notification)
        project_id = self._project_id(payload)
        if project_id is None:
            return
        metadata = payload.get("metadata") or {}
        name = str(metadata.get("project_name") or project_id)
        await self.upsert(tenant_id, project_id, name)

    async def upsert(self, tenant_id: UUID, project_id: UUID, name: str) -> None:
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO research_projects (project_id, tenant_id, name)
                VALUES ($1, $2, $3)
                ON CONFLICT (project_id) DO UPDATE SET name = EXCLUDED.name
                """,
                str(project_id),
                str(tenant_id),
                name,
            )

    def _project_id(self, payload: dict[str, Any]) -> UUID | None:
        metadata = payload.get("metadata") or {}
        raw = metadata.get("project_id")
        if raw is None:
            return None
        try:
            return UUID(str(raw))
        except ValueError:
            logger.warning("Ingested event carries malformed project_id %r", raw)
            return None
