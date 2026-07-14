"""Tests that MCP tool signatures remain stable."""

from __future__ import annotations

import inspect

import pytest

from src.tools import ingest_tools


@pytest.mark.asyncio
async def test_tool_signatures_unchanged() -> None:
    """Ingest tool names and parameter names match a frozen fixture."""
    expected = {
        "ingest_file": {"file_path", "pg_pool", "config", "tenant_id"},
        "ingest_text": {"text", "pg_pool", "source_type", "config", "tenant_id"},
        "ingest_directory": {"directory_path", "pg_pool", "config", "tenant_id"},
        "list_documents": {"pg_pool", "config"},
        "delete_document": {"document_id", "pg_pool", "config"},
    }

    for name, params in expected.items():
        func = getattr(ingest_tools, name)
        sig = inspect.signature(func)
        assert set(sig.parameters.keys()) == params, f"{name} signature changed"
