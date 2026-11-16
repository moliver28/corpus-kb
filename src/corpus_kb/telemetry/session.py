"""Per-session invocation counters (issue #442, owner-confirm D-4).

One session = one wall-clock UTC day: the tracker keys every ordinal by
 ``(method_id, utc_date)``, so a new day starts a new bucket and the
counters reset. ``bump`` is the ONLY mutator (F4); the two readers never
mutate. No session file is persisted, so a restart starts a fresh
session within the same day — the accepted consequence of the
no-persisted-session ruling. The log line records the ordinal and the
first-attempt bit; the day itself never reaches the log.
"""

from __future__ import annotations

from datetime import UTC, datetime


class SessionTracker:
    """Per-method invocation ordinals inside the current UTC-day bucket."""

    def __init__(self) -> None:
        self._ordinals: dict[tuple[str, str], int] = {}

    def bump(self, method_id: str) -> int:
        """Increment the method's ordinal in today's bucket; return it."""
        key = self._key(method_id)
        self._ordinals[key] = self._ordinals.get(key, 0) + 1
        return self._ordinals[key]

    def current_ordinal(self, method_id: str) -> int:
        """The last recorded ordinal for today; ``0`` when none. Read-only."""
        return self._ordinals.get(self._key(method_id), 0)

    def first_attempt(self, method_id: str) -> bool:
        """True only before the method's first bump in this session."""
        return self.current_ordinal(method_id) == 0

    @staticmethod
    def _key(method_id: str) -> tuple[str, str]:
        return (method_id, datetime.now(UTC).date().isoformat())


# The process-wide tracker the emit seam uses (in-process by design, D-4).
_TRACKER = SessionTracker()


def shared_tracker() -> SessionTracker:
    """The process-wide tracker (in-process by design, D-4)."""
    return _TRACKER
