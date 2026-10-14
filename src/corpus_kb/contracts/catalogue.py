"""The operation catalogue: core families (ingest, search, documents, graph).

THE single source of truth for cross-surface operations. The parity gate
(``tests/test_surface_parity_deep.py``) and the inventory command
(``corpus-kb operations``) both iterate these rows, so the test and the
inventory can never diverge. Release rows live in ``catalogue_release.py``,
research rows + the SKIP set in ``catalogue_research.py`` /
``catalogue_skips.py``; ``catalogue_research.OPERATIONS`` is the final
assembled catalogue.

Registry-first rule (program standard 2): to add or change an operation,
change the row FIRST, then the surfaces, then run the gates. All strings
are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.envelopes import INGEST_ENVELOPE
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

# Declared additive HTTP/socket wrapper over the shared ingest envelope:
# the command handler attaches the audit aggregate to successful ingests.
AGGREGATE_EXTRA = frozenset({"aggregate_id", "aggregate_version"})

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

CORE_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="ingest_text",
        family="ingest",
        description="Ingest raw text with a source label and a document type.",
        handler="corpus_kb.tools.ingest_common:ingest_text",
        params=(
            ParamSpec("text", required=True),
            ParamSpec("source", default="raw_text"),
            ParamSpec("source_type", default="text"),
            _TENANT,
        ),
        envelope_keys=INGEST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "ingest_text"),
            SurfaceSpec(HTTP, "/api/ingest/text", "POST"),
            SurfaceSpec(SOCKET, "ingest_text"),
        ),
        http_extra=AGGREGATE_EXTRA,
        socket_extra=AGGREGATE_EXTRA,
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_file",
        family="ingest",
        description="Ingest one file from disk; the document type is auto-detected.",
        handler="corpus_kb.tools.ingest_tools:ingest_file",
        params=(ParamSpec("file_path", required=True), ParamSpec("source_type"), _TENANT),
        envelope_keys=INGEST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "ingest_file"),
            SurfaceSpec(CLI, "ingest <path>"),
            SurfaceSpec(HTTP, "/api/ingest/file", "POST"),
            SurfaceSpec(SOCKET, "ingest_file"),
        ),
        http_extra=AGGREGATE_EXTRA,
        socket_extra=AGGREGATE_EXTRA,
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_url",
        family="ingest",
        description="Fetch (or crawl) a website and ingest its HTML pages.",
        handler="corpus_kb.tools.ingest_url_tools:ingest_url",
        params=(
            ParamSpec("url", required=True),
            ParamSpec("crawl", default=False),
            ParamSpec("source_type"),
            _TENANT,
        ),
        envelope_keys=("status", "pages", "total_documents"),
        surfaces=(
            SurfaceSpec(MCP, "ingest_url"),
            SurfaceSpec(CLI, "ingest-url <url>"),
            SurfaceSpec(HTTP, "/api/ingest/url", "POST"),
            SurfaceSpec(SOCKET, "ingest_url"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_status",
        family="ingest",
        description="Recent ingest ledger entries plus degraded-chunk counts.",
        handler="corpus_kb.tools.ingest_surface_tools:ingest_status",
        params=(ParamSpec("limit", default=20), _TENANT),
        envelope_keys=("status", "ledger_present", "runs", "incomplete_runs", "degraded"),
        surfaces=(
            SurfaceSpec(MCP, "ingest_status"),
            SurfaceSpec(CLI, "ingest-status"),
            SurfaceSpec(HTTP, "/api/ingest/status", "GET"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_reembed",
        family="ingest",
        description="Re-embed degraded (zero-vector) chunks; honest counts.",
        handler="corpus_kb.tools.ingest_surface_tools:ingest_reembed",
        params=(ParamSpec("limit"), ParamSpec("doc_id"), _TENANT),
        envelope_keys=("status", "scanned", "fixed", "failed"),
        surfaces=(
            SurfaceSpec(MCP, "ingest_reembed"),
            SurfaceSpec(CLI, "ingest-reembed"),
            SurfaceSpec(HTTP, "/api/ingest/reembed", "POST"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="search",
        family="search",
        description="Hybrid vector + full-text search with the degraded envelope.",
        handler="corpus_kb.tools.search_tools:search",
        params=(
            ParamSpec("query", required=True),
            ParamSpec("k", default=10),
            ParamSpec("source_type"),
            ParamSpec("self_query"),
            _TENANT,
        ),
        envelope_keys=("status", "result", "degraded_excluded"),
        surfaces=(
            SurfaceSpec(MCP, "search"),
            SurfaceSpec(HTTP, "/api/search", "POST"),
            SurfaceSpec(SOCKET, "search"),
        ),
    ),
    Operation(
        name="query",
        family="search",
        description="Route one question to the vector, SQL, or graph backend.",
        handler="corpus_kb.handlers.router_handler:RouterHandler.handle_routed_query",
        params=(ParamSpec("query", required=True), ParamSpec("k", default=10), _TENANT),
        envelope_keys=("status", "result"),
        surfaces=(SurfaceSpec(HTTP, "/api/query", "POST"), SurfaceSpec(SOCKET, "query")),
    ),
    Operation(
        name="verify_answer",
        family="search",
        description="Check each claim of an answer against the cited chunks.",
        handler="corpus_kb.handlers.query_handler:QueryHandler.handle_verify_answer",
        params=(ParamSpec("answer"), ParamSpec("chunk_ids")),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(HTTP, "/api/verify", "POST"),
            SurfaceSpec(SOCKET, "verify_answer"),
        ),
    ),
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
