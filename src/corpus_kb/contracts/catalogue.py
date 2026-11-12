"""The operation catalogue: search family (ingest rows moved for U3).

THE single source of truth for cross-surface operations. The parity gate
(``tests/test_surface_parity_deep.py``) and the inventory command
(``corpus-kb operations``) both iterate these rows, so the test and the
inventory can never diverge. Ingest rows live in ``catalogue_ingest.py``,
documents/graph rows live in ``catalogue_documents.py``, release rows in
``catalogue_release.py``, research rows + the SKIP set in
``catalogue_research.py``; ``contracts/__init__`` assembles the final
catalogue.

Registry-first rule (program standard 2): to add or change an operation,
change the row FIRST, then the surfaces, then run the gates. All strings
are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.catalogue_ingest import INGEST_OPERATIONS
from corpus_kb.contracts.envelopes import SEARCH_ENVELOPE
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
    *INGEST_OPERATIONS,
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
