"""Every query_crud handler must read inside the tenant transaction.

The pre-fix shapes issued ``set_config`` on an autocommit connection, so
the transaction-local tenant setting evaporated before the read. Any
database whose ``app.current_tenant_id`` default was empty then failed
with ``invalid input syntax for type uuid: ""`` inside the RLS policy —
``list-documents`` crashed on a strict database while returning rows on
a lenient one. These tests pin the transactional shape.
"""

from __future__ import annotations

import inspect
import os

import pytest

import corpus_kb.handlers.query_crud as query_crud

_TEST_DOCS = ["sql_query", "list_documents", "list_entities", "search_context", "verify_answer"]


def test_every_crud_handler_reads_inside_tenant_connection() -> None:
    """Structural: each handler uses the transactional tenant helper."""
    for name in _TEST_DOCS:
        source = inspect.getsource(getattr(query_crud, name))
        assert "tenant_connection" in source, f"{name} bypasses tenant_connection"
        assert "pool.acquire() as conn" not in source.replace("tenant_connection(pool,", ""), (
            f"{name} acquires outside the tenant transaction"
        )


@pytest.mark.requires_postgres
async def test_list_documents_on_strict_database_returns_empty_not_error() -> None:
    """Live: on a scratch DB (empty tenant default), list returns [] without raise."""
    from tests.research_db import create_research_db, drop_research_db

    dsn = await create_research_db()
    try:
        os.environ["CORPUS_KB_DATABASE_URL"] = dsn
        from corpus_kb.cli_ingest import _load_config, _open_pool
        from corpus_kb.tools.ingest_tools import list_documents

        cfg = _load_config()
        pool = await _open_pool(cfg)
        try:
            result = await list_documents(pool, config=cfg, limit=10)
            assert result["status"] == "success"
            assert result["result"] == []
        finally:
            await pool.close()
    finally:
        os.environ.pop("CORPUS_KB_DATABASE_URL", None)
        await drop_research_db()
