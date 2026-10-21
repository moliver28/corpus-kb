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
import corpus_kb.handlers.query_crud_reads as query_crud_reads

# name -> home module. sql_query/list_entities/verify_answer live in
# query_crud_reads (the sp-suite-hardening 250-line split); the pin must
# keep covering all five operations wherever they reside.
_TEST_DOCS = [
    ("sql_query", query_crud_reads),
    ("list_documents", query_crud),
    ("list_entities", query_crud_reads),
    ("search_context", query_crud),
    ("verify_answer", query_crud_reads),
]


def test_every_crud_handler_reads_inside_tenant_connection() -> None:
    """Structural: each handler uses the transactional tenant helper."""
    for name, module in _TEST_DOCS:
        source = inspect.getsource(getattr(module, name))
        assert "tenant_connection" in source, f"{name} bypasses tenant_connection"
        assert "pool.acquire() as conn" not in source.replace("tenant_connection(pool,", ""), (
            f"{name} acquires outside the tenant transaction"
        )


@pytest.mark.requires_postgres
async def test_list_documents_on_strict_database_returns_empty_not_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live: on a scratch DB (empty tenant default), list returns [] without raise."""
    from tests import research_db

    # Do NOT touch the session scratch DB: research_db's unpatched DB_NAME is
    # the one the session research_dsn fixture created, and dropping it here
    # poisons every later research consumer (full-suite ordering poison — see
    # test_research_report_fixture for the established pattern).
    monkeypatch.setattr(research_db, "DB_NAME", "corpus_kb_qcrud_test")
    monkeypatch.setattr(research_db, "DSN", f"{research_db.BASE_DSN}/corpus_kb_qcrud_test")

    dsn = await research_db.create_research_db()
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
        await research_db.drop_research_db()
