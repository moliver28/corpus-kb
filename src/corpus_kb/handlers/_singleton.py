"""The generic handler singleton slot (issue 237).

Eight handler modules repeated the same get/set/reset boilerplate over
a private module global. This holder owns the slot, the empty-slot
raise, and the one-time lazy create. Each handler module keeps its
module-level accessor functions, because tests and wiring reach those
names as module attributes (monkeypatch seams) — only the storage
plumbing moved here.

Sync on purpose: none of the eight slots cache per event loop or per
DSN. Each is one module-scoped value, assigned during startup wiring
(``server_wiring``) or created on first call, and cleared by tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class SingletonSlot(Generic[T]):
    """One module-scoped optional value with get, lazy create, set, reset.

    ``get`` raises ``RuntimeError`` with ``missing_message`` while the
    slot is empty — the raise-style handlers (tag, versioning, coding,
    graph, router) use it directly. The factory-style handlers
    (idempotency, research, command) call ``get_or_create`` with a
    factory, or ``get`` on the no-argument path so an empty slot still
    raises the same message. ``set`` supports startup wiring; ``reset``
    supports tests.
    """

    __slots__ = ("_missing_message", "_value")

    def __init__(self, missing_message: str) -> None:
        self._missing_message = missing_message
        self._value: T | None = None

    def get(self) -> T:
        """Return the stored value; raise RuntimeError while empty."""
        if self._value is None:
            raise RuntimeError(self._missing_message)
        return self._value

    def get_or_create(self, factory: Callable[[], T]) -> T:
        """Return the stored value, creating it once via ``factory``."""
        if self._value is None:
            self._value = factory()
        return self._value

    def set(self, value: T) -> None:
        """Store ``value`` (startup wiring)."""
        self._value = value

    def reset(self) -> None:
        """Clear the slot (tests)."""
        self._value = None
