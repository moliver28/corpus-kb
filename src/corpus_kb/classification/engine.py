"""The stored-vector reader for the classification family (WP-3).

Same contract as ``topics/engine.read_stored_embeddings``: the fit
CONSUMES stored vectors and never calls an embedder. The read is
keyset-paginated in deterministic batches so a 100k-chunk tenant fits
inside a bounded working set (the WP-0 memory model), ordered by chunk
id so every build sees one stable sequence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import asyncpg
import numpy as np

from corpus_kb.storage.tenant_conn import tenant_connection

__all__ = ["StoredVector", "read_stored_vectors"]

# Rows per read batch (20k halfvec(2560) rows is a bounded working set).
READ_BATCH = 20000

# The zero uuid sorts first, so COALESCE makes one query serve both the
# first batch and every keyset-continuation batch.
_UUID_FLOOR = "00000000-0000-0000-0000-000000000000"

_VECTOR_QUERY = f"""
SELECT cv.chunk_id::text AS chunk_id, c.text, cv.embedding::text AS embedding
FROM chunks_vectors cv
JOIN chunks c ON c.chunk_id = cv.chunk_id AND c.tenant_id = cv.tenant_id
WHERE cv.tenant_id = $1::uuid
  AND cv.embedding IS NOT NULL
  AND cv.chunk_id > COALESCE($2::uuid, '{_UUID_FLOOR}'::uuid)
ORDER BY cv.chunk_id
LIMIT $3
"""


@dataclass(frozen=True)
class StoredVector:
    """One chunk text plus its stored embedding (read-only view)."""

    chunk_id: str
    text: str
    vector: np.ndarray  # float32, the parsed halfvec


async def read_stored_vectors(pg_pool: asyncpg.Pool, tenant_id: str) -> list[StoredVector]:
    """Read every embedded chunk for one tenant (tenant GUC, keyset batches).

    Stale rows (``embedding IS NULL``) are excluded: a NULL vector is not
    a classification signal. Ordering is by chunk id, so the fit and the
    scored corpus stay deterministic across builds.
    """
    vectors: list[StoredVector] = []
    async with tenant_connection(pg_pool, tenant_id) as conn:
        last: str | None = None
        while True:
            rows = await conn.fetch(_VECTOR_QUERY, tenant_id, last, READ_BATCH)
            for row in rows:
                vectors.append(
                    StoredVector(
                        chunk_id=str(row["chunk_id"]),
                        text=str(row["text"]),
                        vector=_parse_halfvec_text(str(row["embedding"])),
                    )
                )
            if len(rows) < READ_BATCH:
                return vectors
            last = str(rows[-1]["chunk_id"])


def _parse_halfvec_text(raw: str) -> np.ndarray:
    """pgvector text form ``[0.1,0.2,...]`` is valid JSON; parse to float32."""
    parsed: object = json.loads(raw)
    if not isinstance(parsed, list):
        return np.zeros(0, dtype=np.float32)
    return np.asarray([float(value) for value in parsed], dtype=np.float32)
