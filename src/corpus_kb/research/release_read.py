"""Active-release reads (U24 support) — the SQL/read-model side of the
deduction guard, split from the pure decision logic (adversarial review
F5b: 262-line module, two concerns).

Everything here answers ONE question — which release row is ACTIVE for a
codebook version — through the release layer's read table
(``codebook_releases``, migration 019, projected by ``ReleaseProjector``).
Enforcement SEMANTICS live in :mod:`corpus_kb.research.deduction_guard`;
this module never decides anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import asyncpg

RELEASED_STATE = "released"

ACTIVE_RELEASE_SQL = """
SELECT release_id, state, profile, manifest_sha256, codebook_version_sha256,
       released_at
FROM codebook_releases
WHERE tenant_id = $1 AND codebook_version = $2
ORDER BY (state = 'released') DESC, released_at DESC NULLS LAST, release_id DESC
LIMIT 1
"""


@dataclass(frozen=True)
class ActiveRelease:
    """The release row resolved for one codebook version (may be a draft)."""

    release_id: str
    state: str
    manifest_sha256: str
    codebook_version_sha256: str
    released_at: str | None
    profile: str

    @property
    def is_released(self) -> bool:
        return self.state == RELEASED_STATE


class ReleaseReadConn(Protocol):
    """Minimal asyncpg.Connection surface the release read needs (test-fake
    friendly, mirroring exact_cache.CacheConn)."""

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        """Run one statement, return the first row or None."""
        ...


def active_release_from_row(row: Any) -> ActiveRelease | None:
    """Map one ``codebook_releases`` row (or None) to an ActiveRelease."""
    if row is None:
        return None
    released_at = row["released_at"]
    return ActiveRelease(
        release_id=str(row["release_id"]),
        state=str(row["state"]),
        manifest_sha256=str(row["manifest_sha256"] or ""),
        codebook_version_sha256=str(row["codebook_version_sha256"] or ""),
        released_at=str(released_at) if released_at is not None else None,
        profile=str(row["profile"] or ""),
    )


async def active_release_for_conn(
    conn: ReleaseReadConn, tenant_id: UUID, codebook_version_id: UUID
) -> ActiveRelease | None:
    """The release row resolved for one version under an open tenant scope."""
    row = await conn.fetchrow(
        ACTIVE_RELEASE_SQL, UUID(str(tenant_id)), UUID(str(codebook_version_id))
    )
    return active_release_from_row(row)


async def resolve_active_release(
    pool: asyncpg.Pool, tenant_id: UUID, codebook_version_id: UUID
) -> ActiveRelease | None:
    """Resolve the release row for one version via the release read tables."""
    from typing import cast

    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        # asyncpg.Connection satisfies the read protocol structurally (its
        # stub is untyped, so the cast documents the member, not a lie).
        reader = cast("ReleaseReadConn", conn)
        return await active_release_for_conn(reader, tenant_id, codebook_version_id)
