"""The operation catalogue: tags and metadata (documents family).

The tag/metadata rows (reads: issue #182; writes: Phase-2 Wave 2) live
here, split out of ``catalogue_documents`` for the 250-line rule. The
untag row pins the body-carrying POST /tags/remove shape (gen-default
POST, CORS-safe, no tag-name URL-encoding in the path). Untag of a tag
the document does not carry is a typed not_found error, never a silent
ok. Registry-first rule: change the row first, then the surfaces, then
the gates. All strings are plain English (ASD-STE100).
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

TAGS_OPERATIONS: tuple[Operation, ...] = (
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
)
