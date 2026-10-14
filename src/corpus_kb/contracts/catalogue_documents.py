"""The operation catalogue: documents and graph families.

``list_documents``/``delete_document`` (documents) and
``add_entity``/``add_relation`` (graph) live here. The delete row pins the
Wave 3A unification: the tool's row delete is the ONE implementation; the
HTTP command handler adds the audit aggregate (``AGGREGATE_EXTRA``).
Registry-first rule: change the row first, then the surfaces, then the
gates. All strings are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.envelopes import AGGREGATE_EXTRA
from corpus_kb.contracts.model import (
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    SOCKET,
    Operation,
    ParamSpec,
    SurfaceSpec,
)

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

DOCUMENTS_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="list_documents",
        family="documents",
        description="List ingested documents with pagination.",
        handler="corpus_kb.tools.ingest_tools:list_documents",
        params=(ParamSpec("limit", default=100), ParamSpec("offset", default=0), _TENANT),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "list_documents"),
            SurfaceSpec(HTTP, "/api/documents", "GET"),
            SurfaceSpec(SOCKET, "list_documents"),
        ),
    ),
    Operation(
        name="delete_document",
        family="documents",
        description=(
            "Delete one document by ID. The row delete is idempotent and "
            "tenant-honored; the audit event is the HTTP wrapper's add-on."
        ),
        handler="corpus_kb.tools.ingest_tools:delete_document",
        params=(ParamSpec("document_id", required=True), _TENANT),
        envelope_keys=("status", "document_id"),
        surfaces=(
            SurfaceSpec(MCP, "delete_document"),
            SurfaceSpec(HTTP, "/api/documents/{doc_id}", "DELETE"),
        ),
        http_extra=AGGREGATE_EXTRA,
    ),
    Operation(
        name="add_entity",
        family="graph",
        description="Add one entity to the knowledge graph.",
        handler="corpus_kb.handlers.command_handler:CommandHandler.handle_add_entity",
        params=(
            ParamSpec("name", required=True),
            ParamSpec("entity_type", default="concept"),
            ParamSpec("metadata"),
            _TENANT,
        ),
        envelope_keys=("status", "entity_id", "name", "entity_type"),
        surfaces=(
            SurfaceSpec(HTTP, "/api/entities", "POST"),
            SurfaceSpec(SOCKET, "add_entity"),
        ),
    ),
    Operation(
        name="add_relation",
        family="graph",
        description="Add one relation between two entities.",
        handler="corpus_kb.handlers.command_handler:CommandHandler.handle_add_relation",
        params=(
            ParamSpec("source_entity_id", required=True),
            ParamSpec("target_entity_id", required=True),
            ParamSpec("relation_type", default="related_to"),
            ParamSpec("weight", default=1.0),
            ParamSpec("metadata"),
            _TENANT,
        ),
        envelope_keys=("status", "relation_id", "relation_type"),
        surfaces=(
            SurfaceSpec(HTTP, "/api/relations", "POST"),
            SurfaceSpec(SOCKET, "add_relation"),
        ),
    ),
)
