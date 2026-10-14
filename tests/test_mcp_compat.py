"""Tests that MCP tool signatures remain stable."""

from __future__ import annotations

import inspect

import pytest

from corpus_kb.tools import ingest_tools


@pytest.mark.asyncio
async def test_tool_signatures_unchanged() -> None:
    """Ingest tool names and parameter names match a frozen fixture."""
    expected = {
        # S4: source_type = optional ingestion-strategy override (registry-validated).
        "ingest_file": {"file_path", "pg_pool", "config", "tenant_id", "source_type"},
        "ingest_text": {
            "text",
            "pg_pool",
            "source_type",
            "config",
            "tenant_id",
            "source",
        },
        # W3A unification: recursive joined the signature (default True) so
        # the HTTP recursive flag rides the ONE shared directory walk.
        "ingest_directory": {
            "directory_path",
            "pg_pool",
            "config",
            "tenant_id",
            "source_type",
            "recursive",
        },
        # W1B honesty wave: list_documents delegates to QueryHandler, so it
        # takes the same pagination + tenant arguments as the HTTP/socket
        # surfaces (defaults preserved).
        "list_documents": {"pg_pool", "config", "tenant_id", "limit", "offset"},
        # W1B honesty wave: delete_document honors its tenant (same
        # placeholder default as before).
        "delete_document": {"document_id", "pg_pool", "config", "tenant_id"},
        # S5: ingest front door surfaces (tools/ingest_surface_tools.py).
        "ingest_status": {"pg_pool", "config", "tenant_id", "limit"},
        "ingest_reembed": {"pg_pool", "config", "tenant_id", "limit", "doc_id"},
    }

    for name, params in expected.items():
        func = getattr(ingest_tools, name)
        sig = inspect.signature(func)
        assert set(sig.parameters.keys()) == params, f"{name} signature changed"
