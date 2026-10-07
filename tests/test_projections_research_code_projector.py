"""Tests for the research code_projector (todo 14)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from corpus_kb.projections.research.code_projector import CodeProjector

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
VERSION_ID = UUID("11111111-1111-1111-1111-111111111111")
CODE_ID = "test_code"


def _notification(payload_attrs: dict[str, Any]) -> SimpleNamespace:
    event = SimpleNamespace(**payload_attrs)
    return SimpleNamespace(
        event=event,
        originator_id=VERSION_ID,
        topic="CodebookVersion.PrototypeUpdated",
        notification_id=1,
    )


@pytest.mark.asyncio
async def test_on_prototypes_updated_stores_exemplar_refs_in_theory():
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class FakeConn:
        async def execute(self, sql: str, *args: Any) -> None:
            calls.append((sql, args))

    @asynccontextmanager
    async def fake_tenant_connection(pool: Any, tenant_id: UUID) -> AsyncIterator[FakeConn]:
        yield FakeConn()

    pool = MagicMock()
    projector = CodeProjector(pool)

    payload = {
        "tenant_id": str(TENANT_ID),
        "code_id": CODE_ID,
        "exemplar_text_sha256": ["a" * 64, "b" * 64],
    }

    with patch(
        "corpus_kb.projections.research.code_projector.tenant_connection",
        fake_tenant_connection,
    ):
        await projector.on_prototypes_updated(_notification(payload))

    assert len(calls) == 1
    sql, args = calls[0]
    assert "UPDATE code_registry" in sql
    assert "exemplar_text_sha256" in sql
    assert args[0] == str(TENANT_ID)
    assert args[1] == str(VERSION_ID)
    assert args[2] == CODE_ID
    stored = json.loads(args[3])
    assert stored == ["a" * 64, "b" * 64]
