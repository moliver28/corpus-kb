"""The operation catalogue: the search read family (issue #182).

``search_similar``/``search_context`` complete the search family: both
HTTP routes already existed (``api/routes_search.py``), and these rows
give them the full four-surface parity the catalogue pins. The Ask
screen's Search tab resolves both through the UI manifest.
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

SEARCH_READ_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="search_similar",
        family="search",
        description="Find chunks similar to one stored chunk by vector distance.",
        handler="corpus_kb.tools.search_tools:search_similar",
        params=(
            ParamSpec("chunk_id", required=True),
            ParamSpec("k", default=10),
            _TENANT,
        ),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "search_similar"),
            SurfaceSpec(CLI, "search-similar"),
            SurfaceSpec(HTTP, "/api/search/similar", "POST"),
            SurfaceSpec(SOCKET, "search_similar"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="search_context",
        family="search",
        description="Search with the surrounding context chunks attached.",
        handler="corpus_kb.tools.search_tools:search_context",
        params=(
            ParamSpec("query", required=True),
            ParamSpec("k", default=5),
            ParamSpec("context_chunks", default=2),
            _TENANT,
        ),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "search_context"),
            SurfaceSpec(CLI, "search-context"),
            SurfaceSpec(HTTP, "/api/search/context", "POST"),
            SurfaceSpec(SOCKET, "search_context"),
        ),
        exit_codes=(0, 1, 3),
    ),
)
