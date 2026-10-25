"""Shared fakes for the CLI-leg tests.

``FakePool`` is the minimal pool double the CLI command flows use: they
only ever close it (the tool layer is faked at its own seams).
"""

from __future__ import annotations


class FakePool:
    """Pool double: the CLI flows only ever close it (tools are faked)."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True
