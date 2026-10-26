"""Lifecycle read routes (UI SPA program Task 5b, spec B2).

GET rows in the shared ``research_http`` tool-route table: the Review and
Codebook screens' list reads (projects, codebook versions, proposed codes,
coding assignments), one handler per operation, every surface through the
same tool function. The GET adapter (query parameters, annotation-driven
coercion) and the status mapping live in ``research_http``; this module
owns only the rows, so the manifest, the socket arms, and the HTTP route
table all build from these four entries.
"""

from __future__ import annotations

from corpus_kb.api.research_http import ToolRoute
from corpus_kb.tools import lifecycle_read_tools

__all__ = ["lifecycle_routes"]


def lifecycle_routes() -> tuple[ToolRoute, ...]:
    """The four read-only GET rows (the table probe lives in the tools)."""
    return (
        ToolRoute(
            "/api/projects",
            lifecycle_read_tools.projects_list,
            "pg_pool",
            (),
            ("limit", "tenant_id"),
            "GET",
        ),
        ToolRoute(
            "/api/codebooks",
            lifecycle_read_tools.codebooks_list,
            "pg_pool",
            (),
            ("limit", "tenant_id"),
            "GET",
        ),
        ToolRoute(
            "/api/codebook/proposals",
            lifecycle_read_tools.codebook_proposals,
            "pg_pool",
            (),
            ("status", "limit", "tenant_id"),
            "GET",
        ),
        ToolRoute(
            "/api/coding/assignments",
            lifecycle_read_tools.coding_assignments,
            "pg_pool",
            (),
            ("review_status", "code", "limit", "offset", "tenant_id"),
            "GET",
        ),
    )
