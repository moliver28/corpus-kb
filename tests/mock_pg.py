"""Shared mock helpers for asyncpg-connection fakes in handler tests.

``QueryHandler.handle_search`` runs its reads inside an explicit
``conn.transaction()`` block (tenant GUC + transaction-local HNSW settings,
U20/U40). AsyncMock auto-children make ``mock_conn.transaction()`` return a
COROUTINE, which breaks the ``async with`` — use :func:`transaction_cm` to
wire a working async context manager onto the fake connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def transaction_cm() -> MagicMock:
    """An async context manager standing in for asyncpg Connection.transaction()."""
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    return tx
