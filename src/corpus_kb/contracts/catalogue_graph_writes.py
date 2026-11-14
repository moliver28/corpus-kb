"""The operation catalogue: graph update/delete (Phase-2 Wave 8).

Split out of ``catalogue_graph.py`` for the 250-line rule (the same split
that moved the graph family out of ``catalogue_documents.py``). The four
write ops update or delete one entity or relation. The handler writes
through the GraphStore inside the request path (read-your-writes, no
projection drain). Deletes are hard, confirmed destructive; the envelope
reports the outcome.
Registry-first rule: change the row first, then the surfaces, then the
gates. All strings are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.model import (
    CLI,
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    SOCKET,
    Operation,
    ParamSpec,
    SurfaceSpec,
)

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

GRAPH_WRITE_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="update_entity",
        family="graph",
        description="Update one entity's name and type.",
        handler="corpus_kb.handlers.graph_handler:GraphHandler.handle_update_entity",
        params=(
            ParamSpec("entity_id", required=True),
            ParamSpec("name"),
            ParamSpec("entity_type"),
            ParamSpec("metadata"),
            _TENANT,
        ),
        envelope_keys=("status", "entity_id", "name", "entity_type"),
        surfaces=(
            SurfaceSpec(MCP, "update_entity"),
            SurfaceSpec(CLI, "update-entity"),
            SurfaceSpec(HTTP, "/api/entities/{entity_id}/update", "POST"),
            SurfaceSpec(SOCKET, "update_entity"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="delete_entity",
        family="graph",
        description="Delete one entity and its linked relations.",
        handler="corpus_kb.handlers.graph_handler:GraphHandler.handle_delete_entity",
        params=(
            ParamSpec("entity_id", required=True),
            _TENANT,
        ),
        envelope_keys=("status", "entity_id", "relations_removed"),
        surfaces=(
            SurfaceSpec(MCP, "delete_entity"),
            SurfaceSpec(CLI, "delete-entity"),
            SurfaceSpec(HTTP, "/api/entities/{entity_id}/delete", "POST"),
            SurfaceSpec(SOCKET, "delete_entity"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="update_relation",
        family="graph",
        description="Update one relation's type and weight.",
        handler="corpus_kb.handlers.graph_handler:GraphHandler.handle_update_relation",
        params=(
            ParamSpec("relation_id", required=True),
            ParamSpec("relation_type"),
            ParamSpec("weight"),
            ParamSpec("metadata"),
            _TENANT,
        ),
        envelope_keys=("status", "relation_id", "relation_type"),
        surfaces=(
            SurfaceSpec(MCP, "update_relation"),
            SurfaceSpec(CLI, "update-relation"),
            SurfaceSpec(HTTP, "/api/relations/{relation_id}/update", "POST"),
            SurfaceSpec(SOCKET, "update_relation"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="delete_relation",
        family="graph",
        description="Delete one relation.",
        handler="corpus_kb.handlers.graph_handler:GraphHandler.handle_delete_relation",
        params=(
            ParamSpec("relation_id", required=True),
            _TENANT,
        ),
        envelope_keys=("status", "relation_id", "deleted"),
        surfaces=(
            SurfaceSpec(MCP, "delete_relation"),
            SurfaceSpec(CLI, "delete-relation"),
            SurfaceSpec(HTTP, "/api/relations/{relation_id}/delete", "POST"),
            SurfaceSpec(SOCKET, "delete_relation"),
        ),
        exit_codes=(0, 1, 3),
    ),
)
