"""Tenant advisory write locks for projection write units (sp-projections).

Postgres READ COMMITTED gives the chunkless-relations delete+insert a
duplicate window: two concurrent writers both DELETE zero rows (neither
sees the other's uncommitted INSERT) and both INSERT — NULL chunk_id rows
never meet the unique index. The session-scoped ``pg_try_advisory_lock``
pair (the storage/dialect.py shims) serializes the unit per tenant.
DuckDB allows ONE writer (the file lock plus the in-process transaction
lock), so its shim pair is a no-op that just consumes the bound keys —
one call shape, no engine branch at the call site.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from corpus_kb.errors import ConflictError
from corpus_kb.storage.dialect import Dialect
from corpus_kb.storage.gateway import SqlGateway

_LOCK_ATTEMPTS = 100
_LOCK_POLL_SECONDS = 0.05


@asynccontextmanager
async def tenant_write_lock(
    scope: SqlGateway, dialect: Dialect, tenant_id: str, key: str
) -> AsyncIterator[None]:
    """Hold the tenant advisory lock around one write unit.

    Bounded wait: a rival holder keeps the unit polling for about five
    seconds, then the unit fails typed and the caller's DLQ/replay path
    retries the event. The unlock is best effort: the nested scope keeps
    the Postgres transaction alive after a failed body (an aborted
    transaction refuses every statement), and a dead connection releases
    its session locks server-side anyway.
    """
    lock_sql = dialect.advisory_lock(key, tenant_id)
    unlock_sql = dialect.advisory_unlock(key, tenant_id)
    for _ in range(_LOCK_ATTEMPTS):
        if await scope.fetchval(lock_sql, key, tenant_id):
            break
        await asyncio.sleep(_LOCK_POLL_SECONDS)
    else:
        raise ConflictError(
            "Another writer holds this tenant's relation lock.",
            remediation="Wait for the running ingest to finish. Then replay the event.",
        )
    try:
        # Postgres: a savepoint, so the unlock below still runs after a
        # failed body. DuckDB: a nested scope is a depth-counter no-op.
        async with scope.transaction(tenant_id):
            yield
    finally:
        with suppress(Exception):
            await scope.fetchval(unlock_sql, key, tenant_id)
