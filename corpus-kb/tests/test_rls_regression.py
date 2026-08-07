"""Regression tests for the RLS tenant-context bug: set_config(..., true) must
survive to the query that follows it on the same pooled connection. Each test
exercises a handler or projection class that previously crashed or leaked
tenant context because set_config and the subsequent query were not wrapped
in the same explicit transaction.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.domain.models import SearchQuery
from src.handlers.idempotency import IdempotencyChecker
from src.handlers.query_handler import QueryHandler

pytestmark = pytest.mark.asyncio

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


async def test_query_handler_handle_search_does_not_crash(pg_pool):
    handler = QueryHandler(pg_pool)
    results = await handler.handle_search(
        SearchQuery(tenant_id=UUID(DEFAULT_TENANT_ID), query="anything", k=5)
    )
    assert results == []


async def test_idempotency_check_then_record_does_not_crash(pg_pool):
    checker = IdempotencyChecker(pg_pool)
    tenant_id = UUID(DEFAULT_TENANT_ID)
    command_id = uuid4()

    cached = await checker.check(tenant_id, command_id)
    assert cached is None  # nothing recorded yet

    await checker.record(
        tenant_id, command_id, "TestCommand", {"foo": "bar"}, {"status": "ok"}
    )
    cached_after = await checker.check(tenant_id, command_id)
    assert cached_after is not None
    assert cached_after["command_type"] == "TestCommand"
