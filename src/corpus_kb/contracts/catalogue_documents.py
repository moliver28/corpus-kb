"""The operation catalogue: the documents family.

``list_documents``/``delete_document`` live here. The graph rows (the
writes plus the §5.1 read births and the two window reads) live in
``catalogue_graph.py``. The tags/metadata rows (reads: issue #182;
writes: Phase-2 Wave 2) live in ``catalogue_tags.py``. The delete row
pins the Wave 3A unification:
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
    # -- Phase-2 Wave 5: the drawer's NLP panel scopes its analysis runs
    #    to one document's chunks. Read-only window read; the registry id
    #    is the UI manifest id (chunks-list). --------------------------------
    Operation(
        name="chunks_list",
        family="documents",
        description="List one document's chunks in ingest order (read-only).",
        handler="corpus_kb.tools.chunk_read_tools:chunks_list",
        params=(
            ParamSpec("doc_id", required=True),
            ParamSpec("limit", default=100),
            ParamSpec("offset", default=0),
            _TENANT,
        ),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "chunks_list"),
            SurfaceSpec(CLI, "chunks-list"),
            SurfaceSpec(HTTP, "/api/documents/{doc_id}/chunks", "GET"),
            SurfaceSpec(SOCKET, "chunks_list"),
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
    )
)
