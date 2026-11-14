"""The Wave-8 graph U/D handler methods (the ``graph_handler`` split).

Split out of ``graph_handler.py`` for the 250-line rule, as a mixin: the
class declares ``_graph_store`` and contributes only the four methods.
Each method writes through the GraphStore INSIDE the request path: the
row write commits before the answer is built, so the next read already
sees it (read-your-writes without a projection drain — the judged
Wave-8 divergence from the create path's ``_materialize``).

There is NO audit trail and NO undo for these writes, by decision: the
entity/relation aggregate streams carry only Created events and the
projection has no Updated/Deleted replay arms — a half-audit would be a
false audit. Deletes are hard, confirmed destructive, and idempotent;
the envelope reports the outcome.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.errors import NotFoundError, ValidationError
from corpus_kb.storage.graph_store import GraphStore
from corpus_kb.utils.models import Entity, Relation


class GraphUdMixin:
    """The four Wave-8 U/D methods on the graph handler."""

    _graph_store: GraphStore

    async def handle_update_entity(
        self,
        tenant_id: UUID,
        entity_id: UUID,
        *,
        name: str | None = None,
        entity_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update one entity's name and type (and metadata, whole-replace).

        Present fields overwrite; absent fields keep the stored values.
        404 when the row vanished under an open form. A (tenant, name,
        type) collision with a different entity raises ConflictError.
        """
        stored = await self._graph_store.get_entity(str(entity_id))
        if stored is None:
            raise NotFoundError(f"Entity {entity_id} does not exist.")
        merged = Entity(
            entity_id=str(entity_id),
            name=name if name is not None else stored.name,
            entity_type=entity_type if entity_type is not None else stored.entity_type,
            source_type="code",
            source_document_id=stored.source_document_id,
            metadata=metadata if metadata is not None else stored.metadata,
        )
        saved = await self._graph_store.update_entity(merged)
        if saved is None:
            raise NotFoundError(f"Entity {entity_id} does not exist.")
        return {
            "status": "success",
            "entity_id": saved.entity_id,
            "name": saved.name,
            "entity_type": saved.entity_type,
        }

    async def handle_delete_entity(self, tenant_id: UUID, entity_id: UUID) -> dict[str, Any]:
        """Delete one entity plus every relation linked to it.

        Idempotent: an already-gone id answers success with
        ``relations_removed`` 0. The response reports the exact count of
        relations the server removed (they may exceed what the open
        window showed).
        """
        removed = await self._graph_store.delete_entity(str(entity_id))
        return {
            "status": "success",
            "entity_id": str(entity_id),
            "relations_removed": removed if removed is not None else 0,
        }

    async def handle_update_relation(
        self,
        tenant_id: UUID,
        relation_id: UUID,
        *,
        relation_type: str | None = None,
        weight: float | None = None,
        metadata: dict[str, Any] | None = None,
        source_entity_id: UUID | str | None = None,
        target_entity_id: UUID | str | None = None,
    ) -> dict[str, Any]:
        """Update one relation's type, weight, and metadata.

        Endpoints are immutable: a changed pair is a new triple — delete
        the relation, then create the new one. ``weight`` must be greater
        than 0. 404 when the relation does not exist.
        """
        stored = await self._graph_store.get_relation(str(relation_id))
        if stored is None:
            raise NotFoundError(f"Relation {relation_id} does not exist.")
        if source_entity_id is not None and str(source_entity_id) != stored.source_entity_id:
            raise ValidationError(
                "Endpoints cannot change. Delete the relation, then create the new one."
            )
        if target_entity_id is not None and str(target_entity_id) != stored.target_entity_id:
            raise ValidationError(
                "Endpoints cannot change. Delete the relation, then create the new one."
            )
        if weight is not None and weight <= 0:
            raise ValidationError("weight must be greater than 0.")
        saved = await self._graph_store.update_relation(
            Relation(
                relation_id=str(relation_id),
                source_entity_id=stored.source_entity_id,
                target_entity_id=stored.target_entity_id,
                relation_type=relation_type if relation_type is not None else stored.relation_type,
                weight=weight if weight is not None else stored.weight,
                metadata=metadata if metadata is not None else stored.metadata,
            )
        )
        if saved is None:
            raise NotFoundError(f"Relation {relation_id} does not exist.")
        return {
            "status": "success",
            "relation_id": saved.relation_id,
            "relation_type": saved.relation_type,
        }

    async def handle_delete_relation(self, tenant_id: UUID, relation_id: UUID) -> dict[str, Any]:
        """Delete one relation by ID. ``deleted`` reports the honest outcome."""
        deleted = await self._graph_store.delete_relation(str(relation_id))
        return {
            "status": "success",
            "relation_id": str(relation_id),
            "deleted": deleted,
        }
