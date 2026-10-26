"""The operation catalogue: ingest and search families.

THE single source of truth for cross-surface operations. The parity gate
(``tests/test_surface_parity_deep.py``) and the inventory command
(``corpus-kb operations``) both iterate these rows, so the test and the
inventory can never diverge. Documents/graph rows live in
``catalogue_documents.py``, release rows in ``catalogue_release.py``,
research rows + the SKIP set in ``catalogue_research.py``;
``contracts/__init__`` assembles the final catalogue.

Registry-first rule (program standard 2): to add or change an operation,
change the row FIRST, then the surfaces, then run the gates. All strings
are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.envelopes import (
    AGGREGATE_EXTRA,
    DEDUP_KEYS,
    INGEST_ENVELOPE,
    SEARCH_ENVELOPE,
    VALIDATION_KEYS,
)
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
            ParamSpec("validate", default="off"),
            _TENANT,
        ),
        envelope_keys=INGEST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "ingest_text"),
            SurfaceSpec(CLI, "ingest-text"),
            SurfaceSpec(HTTP, "/api/ingest/text", "POST"),
            SurfaceSpec(SOCKET, "ingest_text"),
        ),
        http_extra=AGGREGATE_EXTRA | VALIDATION_KEYS | DEDUP_KEYS,
        socket_extra=AGGREGATE_EXTRA | VALIDATION_KEYS | DEDUP_KEYS,
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="ingest_file",
        family="ingest",
        description="Ingest one file from disk; the document type is auto-detected.",
        handler="corpus_kb.tools.ingest_tools:ingest_file",
        params=(
            ParamSpec("file_path", required=True),
            ParamSpec("source_type"),
            ParamSpec("validate", default="off"),
            _TENANT,
        ),
        envelope_keys=INGEST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "ingest_file"),
            SurfaceSpec(CLI, "ingest <path>"),
            SurfaceSpec(HTTP, "/api/ingest/file", "POST"),
            SurfaceSpec(SOCKET, "ingest_file"),
        ),
        http_extra=AGGREGATE_EXTRA | VALIDATION_KEYS | DEDUP_KEYS,
        socket_extra=AGGREGATE_EXTRA | VALIDATION_KEYS | DEDUP_KEYS,
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_directory",
        family="ingest",
        description=(
            "Ingest the supported files under one directory in one walk. "
            "One filter: the WATCH_SUFFIXES modality table."
        ),
        handler="corpus_kb.tools.ingest_tools:ingest_directory",
        params=(
            ParamSpec("directory_path", required=True),
            ParamSpec("recursive", default=True),
            ParamSpec("source_type"),
            ParamSpec("validate", default="off"),
            _TENANT,
        ),
        envelope_keys=(
            "status",
            "directory",
            "files_processed",
            "total_documents",
            "total_entities",
            "results",
        ),
        surfaces=(
            SurfaceSpec(MCP, "ingest_directory"),
            SurfaceSpec(CLI, "ingest <dir>"),
            SurfaceSpec(HTTP, "/api/ingest/directory", "POST"),
            SurfaceSpec(SOCKET, "ingest_directory"),
        ),
        http_extra=VALIDATION_KEYS | DEDUP_KEYS,
        socket_extra=VALIDATION_KEYS | DEDUP_KEYS,
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
            ParamSpec("validate", default="off"),
            _TENANT,
        ),
        envelope_keys=("status", "pages", "total_documents"),
        http_extra=VALIDATION_KEYS,
        socket_extra=VALIDATION_KEYS,
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
            SurfaceSpec(SOCKET, "ingest_status"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_reembed",
        family="ingest",
        description="Re-embed degraded (zero-vector) and stale (NULL) chunks; honest counts.",
        handler="corpus_kb.tools.ingest_surface_tools:ingest_reembed",
        params=(ParamSpec("limit"), ParamSpec("doc_id"), _TENANT),
        envelope_keys=("status", "scanned", "fixed", "failed"),
        surfaces=(
            SurfaceSpec(MCP, "ingest_reembed"),
            SurfaceSpec(CLI, "ingest-reembed"),
            SurfaceSpec(HTTP, "/api/ingest/reembed", "POST"),
            SurfaceSpec(SOCKET, "ingest_reembed"),
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
            # W3: the pinned default k mirrors reranking.return_top_k (the
            # runtime default when a caller omits k); the lockstep test in
            # tests/test_config_blocks.py fails if they drift.
            ParamSpec("k", default=20),
            ParamSpec("source_type"),
            ParamSpec("self_query"),
            _TENANT,
        ),
        envelope_keys=SEARCH_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "search"),
            SurfaceSpec(CLI, "search"),
            SurfaceSpec(HTTP, "/api/search", "POST"),
            SurfaceSpec(SOCKET, "search"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="query",
        family="search",
        description="Route one question to the vector, SQL, or graph backend.",
        handler="corpus_kb.handlers.router_handler:RouterHandler.handle_routed_query",
        params=(ParamSpec("query", required=True), ParamSpec("k", default=20), _TENANT),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "query"),
            SurfaceSpec(CLI, "query"),
            SurfaceSpec(HTTP, "/api/query", "POST"),
            SurfaceSpec(SOCKET, "query"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="verify_answer",
        family="search",
        description="Check each claim of an answer against the cited chunks.",
        handler="corpus_kb.handlers.query_handler:QueryHandler.handle_verify_answer",
        params=(ParamSpec("answer"), ParamSpec("chunk_ids")),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "verify_answer"),
            SurfaceSpec(CLI, "verify-answer"),
            SurfaceSpec(HTTP, "/api/verify", "POST"),
            SurfaceSpec(SOCKET, "verify_answer"),
        ),
        exit_codes=(0, 1, 3),
    ),
)
