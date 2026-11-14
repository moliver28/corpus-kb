"""The Wave-8 graph U/D socket arms (the ``_socket_arms`` split, 250-line rule).

Each arm parses the method params and calls the ONE graph handler every
surface routes through. The envelope is exactly what
POST /api/entities/{id}/update (and its three siblings) return.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


async def _update_entity(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.handlers.graph_handler import get_graph_handler

    return await get_graph_handler().handle_update_entity(
        UUID(tenant_id),
        UUID(params["entity_id"]),
        name=params.get("name"),
        entity_type=params.get("entity_type"),
        metadata=params.get("metadata"),
    )


async def _delete_entity(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.handlers.graph_handler import get_graph_handler

    return await get_graph_handler().handle_delete_entity(
        UUID(tenant_id),
        UUID(params["entity_id"]),
    )


async def _update_relation(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.handlers.graph_handler import get_graph_handler

    return await get_graph_handler().handle_update_relation(
        UUID(tenant_id),
        UUID(params["relation_id"]),
        relation_type=params.get("relation_type"),
        weight=params.get("weight"),
        metadata=params.get("metadata"),
    )


async def _delete_relation(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.handlers.graph_handler import get_graph_handler

    return await get_graph_handler().handle_delete_relation(
        UUID(tenant_id),
        UUID(params["relation_id"]),
    )
