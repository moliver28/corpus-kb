"""SingletonSlot unit tests (issue 237: the generic handler holder).

The holder replaces eight copies of the get/set/reset boilerplate in
the handler modules. These tests pin its contract: empty-slot raise
with the module's own message, one-time lazy create, startup set, and
the test reset.
"""

from __future__ import annotations

import pytest

from corpus_kb.handlers._singleton import SingletonSlot


def _slot() -> SingletonSlot[str]:
    return SingletonSlot[str]("Handler not initialized. Call set_handler() during startup.")


def test_get_on_empty_slot_raises_the_missing_message() -> None:
    slot = _slot()
    with pytest.raises(RuntimeError, match="Handler not initialized"):
        slot.get()


def test_set_then_get_returns_the_value() -> None:
    slot = _slot()
    slot.set("value")
    assert slot.get() == "value"


def test_reset_clears_the_slot() -> None:
    slot = _slot()
    slot.set("value")
    slot.reset()
    with pytest.raises(RuntimeError, match="Handler not initialized"):
        slot.get()


def test_get_or_create_builds_once_and_reuses_the_instance() -> None:
    slot: SingletonSlot[list[int]] = SingletonSlot[list[int]]("empty")
    calls: list[int] = []

    def factory() -> list[int]:
        calls.append(1)
        return [7]

    first = slot.get_or_create(factory)
    second = slot.get_or_create(factory)
    assert first is second
    assert calls == [1]


def test_get_or_create_returns_existing_value_without_running_factory() -> None:
    slot = SingletonSlot[str]("empty")
    slot.set("value")
    calls: list[str] = []

    def factory() -> str:
        calls.append("ran")
        return "fresh"

    assert slot.get_or_create(factory) == "value"
    assert calls == []
