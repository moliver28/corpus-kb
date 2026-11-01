"""Handler singleton accessor contracts (issue 237).

The eight handler modules expose module-level get/set/reset functions
over a SingletonSlot; tests and wiring patch those module attributes, so
the functions themselves stay the seam. These tests call the REAL
accessors (no monkeypatching) to pin each family's contract: empty-slot
raise with the exact message, set/get round trip, reset, and the
lazy-create path for the factory-style handlers. The singletons always
start and end reset, so no state leaks across tests.
"""

from __future__ import annotations

from types import ModuleType
from typing import cast

import asyncpg
import pytest

from corpus_kb.handlers import (
    coding_handler,
    graph_handler,
    idempotency,
    research_handler,
    router_handler,
    tag_handler,
    versioning_handler,
)
from corpus_kb.handlers.idempotency import IdempotencyChecker

_RAISE_STYLE_FAMILIES: list[tuple[ModuleType, str, str, str, str]] = [
    (
        tag_handler,
        "get_tag_handler",
        "set_tag_handler",
        "reset_tag_handler",
        "TagHandler not initialized",
    ),
    (
        versioning_handler,
        "get_versioning_handler",
        "set_versioning_handler",
        "reset_versioning_handler",
        "VersioningHandler not initialized",
    ),
    (
        coding_handler,
        "get_coding_handler",
        "set_coding_handler",
        "reset_coding_handler",
        "CodingHandler not initialized",
    ),
    (
        graph_handler,
        "get_graph_handler",
        "set_graph_handler",
        "reset_graph_handler",
        "GraphHandler not initialized",
    ),
    (
        router_handler,
        "get_router_handler",
        "set_router_handler",
        "reset_router_handler",
        "RouterHandler not initialized",
    ),
]


@pytest.mark.parametrize(
    ("module", "get_name", "set_name", "reset_name", "message"),
    _RAISE_STYLE_FAMILIES,
)
def test_raise_style_family_contract(
    module: ModuleType, get_name: str, set_name: str, reset_name: str, message: str
) -> None:
    getattr(module, reset_name)()
    try:
        with pytest.raises(RuntimeError, match=message):
            getattr(module, get_name)()
        sentinel = cast(object, object())
        getattr(module, set_name)(sentinel)
        assert getattr(module, get_name)() is sentinel
    finally:
        getattr(module, reset_name)()
    with pytest.raises(RuntimeError, match=message):
        getattr(module, get_name)()


def test_idempotency_checker_without_pool_raises_on_empty_slot() -> None:
    idempotency.reset_idempotency_checker()
    try:
        with pytest.raises(RuntimeError, match="requires an asyncpg pool"):
            idempotency.get_idempotency_checker()
    finally:
        idempotency.reset_idempotency_checker()


def test_idempotency_checker_lazy_create_set_and_reset() -> None:
    idempotency.reset_idempotency_checker()
    dummy_pool = cast(asyncpg.Pool, object())
    try:
        first = idempotency.get_idempotency_checker(dummy_pool)
        second = idempotency.get_idempotency_checker(dummy_pool)
        assert first is second
        idempotency.reset_idempotency_checker()
        with pytest.raises(RuntimeError, match="requires an asyncpg pool"):
            idempotency.get_idempotency_checker()
        stub = cast(IdempotencyChecker, object())
        idempotency.set_idempotency_checker(stub)
        assert idempotency.get_idempotency_checker() is stub
    finally:
        idempotency.reset_idempotency_checker()


def test_research_handler_without_pool_raises_on_empty_slot() -> None:
    research_handler.reset_research_handler()
    try:
        with pytest.raises(RuntimeError, match="requires an asyncpg pool"):
            research_handler.get_research_handler()
    finally:
        research_handler.reset_research_handler()


def test_research_handler_lazy_create_and_no_pool_returns_existing() -> None:
    research_handler.reset_research_handler()
    dummy_pool = cast(asyncpg.Pool, object())
    try:
        first = research_handler.get_research_handler(dummy_pool)
        assert research_handler.get_research_handler() is first
        research_handler.reset_research_handler()
        with pytest.raises(RuntimeError, match="requires an asyncpg pool"):
            research_handler.get_research_handler()
    finally:
        research_handler.reset_research_handler()
