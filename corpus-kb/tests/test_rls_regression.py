"""Regression tests for the RLS tenant-context bug: set_config(..., true) must
survive to the query that follows it on the same pooled connection. Each test
exercises a handler or projection class that previously crashed or leaked
tenant context because set_config and the subsequent query were not wrapped
in the same explicit transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.domain.models import SearchQuery
from src.handlers.idempotency import IdempotencyChecker
from src.handlers.query_handler import QueryHandler
from src.handlers.graph_handler import GraphHandler
from src.handlers.tag_handler import TagHandler
from src.handlers.versioning_handler import VersioningHandler
from src.projections.checkpoint import CheckpointManager
from src.projections.dlq import DLQHandler

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


async def test_graph_handler_search_graph_does_not_crash(pg_pool):
    handler = GraphHandler(pg_pool)
    results = await handler.handle_search_graph(UUID(DEFAULT_TENANT_ID), "anything")
    assert results == []


async def test_tag_handler_add_tag_does_not_crash(pg_pool):
    handler = TagHandler(pg_pool)
    tenant_id = UUID(DEFAULT_TENANT_ID)
    tag_name = f"test-tag-{uuid4().hex[:8]}"
    result = await handler.handle_add_tag(tenant_id, tag_name)
    assert result.get("name") == tag_name


async def test_versioning_handler_get_stats_does_not_crash(pg_pool):
    handler = VersioningHandler(pg_pool)
    stats = await handler.handle_get_stats(UUID(DEFAULT_TENANT_ID))
    assert "documents" in stats


async def test_checkpoint_manager_update_then_get_does_not_crash(pg_pool):
    mgr = CheckpointManager(pg_pool)
    tenant_id = UUID(DEFAULT_TENANT_ID)
    event_id = uuid4()
    event_timestamp = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    await mgr.update_checkpoint("TestProjection", tenant_id, event_id, event_timestamp)
    checkpoint = await mgr.get_checkpoint("TestProjection", tenant_id)
    assert checkpoint is not None
    assert str(checkpoint["last_event_id"]) == str(event_id)


async def test_dlq_handler_record_then_list_does_not_crash(pg_pool):
    dlq = DLQHandler(pg_pool)
    tenant_id = UUID(DEFAULT_TENANT_ID)
    event_id = uuid4()

    await dlq.record_failure(
        "TestProjection", tenant_id, event_id, "TestEvent", "boom"
    )
    failures = await dlq.list_failures("TestProjection", tenant_id)
    assert any(str(f["event_id"]) == str(event_id) for f in failures)
