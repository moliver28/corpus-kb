"""Tests for the verify_answer groundedness endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.domain.models import VerifyAnswerQuery
from src.handlers.query_handler import QueryHandler


@pytest.mark.asyncio
async def test_verify_answer_abstains_with_no_chunk_ids() -> None:
    handler = QueryHandler(pool=MagicMock())
    result = await handler.handle_verify_answer(VerifyAnswerQuery(answer="anything", chunk_ids=[]))
    assert result.abstained is True
    assert result.groundedness == 0.0
