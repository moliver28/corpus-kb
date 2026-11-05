"""The operation catalogue: documents and graph families.

``list_documents``/``delete_document`` plus the tags/metadata reads
(``get_document_tags``/``get_metadata``, issue #182) and the graph rows
(``add_entity``/``add_relation`` plus the §5.1 read births
``search_graph``/``get_entity_relations``/``graph_neighbors``/
``graph_traverse``) live here. The delete row pins the Wave 3A unification:
the tool's row delete is the ONE implementation; the HTTP command handler
adds the audit aggregate (``AGGREGATE_EXTRA``).
Registry-first rule: change the row first, then the surfaces, then the
gates. All strings are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.envelopes import AGGREGATE_EXTRA
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
            SurfaceSpec(CLI, "list-documents"),
            SurfaceSpec(HTTP, "/api/documents", "GET"),
            SurfaceSpec(SOCKET, "list_documents"),
        ),
        exit_codes=(0, 1, 3),
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
            SurfaceSpec(CLI, "delete-document"),
            SurfaceSpec(HTTP, "/api/documents/{doc_id}", "DELETE"),
            SurfaceSpec(SOCKET, "delete_document"),
        ),
        http_extra=AGGREGATE_EXTRA,
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="get_document_tags",
        family="documents",
        description="List one document's tags. Rows carry name, color, description.",
        handler="corpus_kb.tools.tag_tools:get_document_tags",
        params=(ParamSpec("doc_id", required=True), _TENANT),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "get_document_tags"),
            SurfaceSpec(CLI, "get-document-tags"),
            SurfaceSpec(HTTP, "/api/documents/{doc_id}/tags", "GET"),
            SurfaceSpec(SOCKET, "get_document_tags"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="get_metadata",
        family="documents",
        description="Read metadata rows by key or document. Rows carry key and value.",
        handler="corpus_kb.tools.tag_tools:get_metadata",
        params=(ParamSpec("key"), ParamSpec("doc_id"), _TENANT),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "get_metadata"),
            SurfaceSpec(CLI, "get-metadata"),
            SurfaceSpec(HTTP, "/api/metadata", "GET"),
            SurfaceSpec(SOCKET, "get_metadata"),
        ),
        exit_codes=(0, 1, 3),
    ),
    # -- Phase-2 Wave 2 write ops: the drawer's tag and metadata edits. -----
    # The untag row pins the body-carrying POST /tags/remove shape (gen-default
    # POST, CORS-safe, no tag-name URL-encoding in the path). Untag of a tag
    # the document does not carry is a typed not_found error, never a silent ok.
    Operation(
        name="add_tag",
        family="documents",
        description="Create one tag in the tag vocabulary.",
        handler="corpus_kb.tools.tag_tools:add_tag",
        params=(
            ParamSpec("name", required=True),
            ParamSpec("color"),
            ParamSpec("description"),
            _TENANT,
        ),
        envelope_keys=("status", "tag_id", "name"),
        surfaces=(
            SurfaceSpec(MCP, "add_tag"),
            SurfaceSpec(CLI, "add-tag"),
            SurfaceSpec(HTTP, "/api/tags", "POST"),
            SurfaceSpec(SOCKET, "add_tag"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="tag_document",
        family="documents",
        description="Apply one tag to a document. A missing tag is created first.",
        handler="corpus_kb.tools.tag_tools:tag_document",
        params=(ParamSpec("doc_id", required=True), ParamSpec("tag", required=True), _TENANT),
        envelope_keys=("status", "doc_id", "tag"),
        surfaces=(
            SurfaceSpec(MCP, "tag_document"),
            SurfaceSpec(CLI, "tag-document"),
            SurfaceSpec(HTTP, "/api/documents/{doc_id}/tags", "POST"),
            SurfaceSpec(SOCKET, "tag_document"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="untag_document",
        family="documents",
        description="Remove one tag from a document. A missing link is a typed error.",
        handler="corpus_kb.tools.tag_tools:untag_document",
        params=(ParamSpec("doc_id", required=True), ParamSpec("tag", required=True), _TENANT),
        envelope_keys=("status", "doc_id", "tag"),
        surfaces=(
            SurfaceSpec(MCP, "untag_document"),
            SurfaceSpec(CLI, "untag-document"),
            SurfaceSpec(HTTP, "/api/documents/{doc_id}/tags/remove", "POST"),
            SurfaceSpec(SOCKET, "untag_document"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="set_metadata",
        family="documents",
        description="Set one metadata key for a document. The write is an upsert.",
        handler="corpus_kb.tools.tag_tools:set_metadata",
        params=(
            ParamSpec("key", required=True),
            ParamSpec("value", required=True),
            ParamSpec("doc_id"),
            _TENANT,
        ),
        envelope_keys=("status", "key", "value"),
        surfaces=(
            SurfaceSpec(MCP, "set_metadata"),
            SurfaceSpec(CLI, "set-metadata"),
            SurfaceSpec(HTTP, "/api/metadata", "POST"),
            SurfaceSpec(SOCKET, "set_metadata"),
        ),
        exit_codes=(0, 1, 3),
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
            SurfaceSpec(MCP, "add_entity"),
            SurfaceSpec(CLI, "add-entity"),
            SurfaceSpec(HTTP, "/api/entities", "POST"),
            SurfaceSpec(SOCKET, "add_entity"),
        ),
        exit_codes=(0, 1, 3),
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
            SurfaceSpec(MCP, "add_relation"),
            SurfaceSpec(CLI, "add-relation"),
            SurfaceSpec(HTTP, "/api/relations", "POST"),
            SurfaceSpec(SOCKET, "add_relation"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="search_graph",
        family="graph",
        description="Search graph entities by name. Rows carry name and type.",
        handler="corpus_kb.tools.graph_read_tools:search_graph",
        params=(
            ParamSpec("query", required=True),
            ParamSpec("entity_type"),
            ParamSpec("limit", default=100),
            _TENANT,
        ),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "search_graph"),
            SurfaceSpec(CLI, "search-graph"),
            SurfaceSpec(HTTP, "/api/graph/search", "POST"),
            SurfaceSpec(SOCKET, "search_graph"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="get_entity_relations",
        family="graph",
        description="List one entity's relations. Rows carry both endpoint names.",
        handler="corpus_kb.tools.graph_read_tools:get_entity_relations",
        params=(
            ParamSpec("entity_id", required=True),
            ParamSpec("limit"),
            _TENANT,
        ),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "get_entity_relations"),
            SurfaceSpec(CLI, "get-entity-relations"),
            SurfaceSpec(HTTP, "/api/graph/relations/{entity_id}", "GET"),
            SurfaceSpec(SOCKET, "get_entity_relations"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="graph_neighbors",
        family="graph",
        description="List entities within 1 to 3 hops. Rows carry their depth.",
        handler="corpus_kb.tools.graph_read_tools:graph_neighbors",
        params=(
            ParamSpec("entity_id", required=True),
            ParamSpec("hops", default=1),
            ParamSpec("direction", default="both"),
            ParamSpec("relation_types"),
            ParamSpec("entity_types"),
            ParamSpec("limit", default=100),
            _TENANT,
        ),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "graph_neighbors"),
            SurfaceSpec(CLI, "graph-neighbors"),
            SurfaceSpec(HTTP, "/api/graph/neighbors", "POST"),
            SurfaceSpec(SOCKET, "graph_neighbors"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="graph_traverse",
        family="graph",
        description="Walk the graph from one entity. Rows carry their depth.",
        handler="corpus_kb.tools.graph_read_tools:graph_traverse",
        params=(
            ParamSpec("entity_id", required=True),
            ParamSpec("max_depth"),
            ParamSpec("relation_types"),
            ParamSpec("entity_types"),
            ParamSpec("limit", default=100),
            _TENANT,
        ),
        envelope_keys=("status", "result", "truncated"),
        surfaces=(
            SurfaceSpec(MCP, "graph_traverse"),
            SurfaceSpec(CLI, "graph-traverse"),
            SurfaceSpec(HTTP, "/api/graph/traverse", "POST"),
            SurfaceSpec(SOCKET, "graph_traverse"),
        ),
        exit_codes=(0, 1, 3),
    ),
)
