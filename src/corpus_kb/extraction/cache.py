"""Chunk-hash extraction cache (asyncpg, RLS-enforced).

Keyed by ``(tenant_id, chunk_hash, backend, model_version,
prompt_version)``. A hit skips the model pass for that chunk and is
recorded in ``ExtractStats.notes`` — the honest-count contract. Empty
entity/relation lists are valid hits (the model genuinely found
nothing). Payloads cross the wire as JSON, so dumps use
``model_dump(mode="json")`` — python-mode keeps UUID objects that neither
JSONB nor JSONResponse accept.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from ..utils.models import Entity, Relation

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_GET_SQL = """
SELECT chunk_hash, entities, relations
FROM extraction_cache
WHERE chunk_hash = ANY($1::text[]) AND backend = $2
  AND model_version = $3 AND prompt_version = $4
"""

_EXISTING_SQL = """
SELECT chunk_hash FROM extraction_cache
WHERE chunk_hash = ANY($1::text[]) AND backend = $2
  AND model_version = $3 AND prompt_version = $4
"""

_CACHE_COLUMNS = [
    "tenant_id",
    "chunk_hash",
    "backend",
    "model_version",
    "prompt_version",
    "entities",
    "relations",
]


def _payload(value: list[Entity] | list[Relation]) -> str:
    return json.dumps([item.model_dump(mode="json") for item in value])


class ExtractionCache:
    """Read-through cache of per-chunk extraction results."""

    def __init__(self, pool: asyncpg.Pool, tenant_id: str) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def get(
        self,
        chunk_hashes: list[str],
        *,
        backend: str,
        model_version: str,
        prompt_version: str,
    ) -> dict[str, tuple[list[Entity], list[Relation]]]:
        """Cached results keyed by chunk hash; absent hashes are misses."""
        if not chunk_hashes:
            return {}
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            rows = await conn.fetch(_GET_SQL, chunk_hashes, backend, model_version, prompt_version)
        hits: dict[str, tuple[list[Entity], list[Relation]]] = {}
        for row in rows:
            try:
                entities = [Entity.model_validate(item) for item in json.loads(row["entities"])]
                relations = [Relation.model_validate(item) for item in json.loads(row["relations"])]
            except Exception as exc:
                logger.warning("Skipping unreadable extraction_cache row: %s", exc)
                continue
            hits[row["chunk_hash"]] = (entities, relations)
        return hits

    async def put(
        self,
        entries: dict[str, tuple[list[Entity], list[Relation]]],
        *,
        backend: str,
        model_version: str,
        prompt_version: str,
    ) -> int:
        """Store per-chunk results; returns rows written.

        Existing rows are pre-checked and skipped, so a concurrent writer
        can never trip the primary key: the cache is write-once per key.
        """
        if not entries:
            return 0
        hashes = list(entries)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            stored = await conn.fetch(_EXISTING_SQL, hashes, backend, model_version, prompt_version)
            fresh = [h for h in hashes if h not in {r["chunk_hash"] for r in stored}]
            records = [
                (
                    self._tenant_id,
                    chunk_hash,
                    backend,
                    model_version,
                    prompt_version,
                    _payload(entries[chunk_hash][0]),
                    _payload(entries[chunk_hash][1]),
                )
                for chunk_hash in fresh
            ]
            if records:
                await conn.copy_records_to_table(
                    "extraction_cache", records=records, columns=_CACHE_COLUMNS
                )
        return len(records)
