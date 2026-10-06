"""Codebook loading: resolve the version, embed off-transaction, write the registry.

Split out of codebook.py (validation, hashing, theory grounding) because the load
is three phases with a transaction boundary between them, and carrying that
structure plus the degrade guard here keeps both files inside the 250-line limit.

Three phases, mirroring coder_dispatch.dispatch_chunk: a short transaction that
resolves the codebook version, the embedding calls with NO transaction open, then
one short transaction that writes code_registry. Each embed is a network round
trip, so holding a pooled connection idle-in-transaction across a whole codebook
would exhaust the pool under any concurrent load.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.coding.codebook import CodebookError, codebook_sha256, validate_codebook
from corpus_kb.storage.tenant_conn import tenant_connection

_INSERT_CODE = """
INSERT INTO code_registry
  (code_id, codebook_version_id, tenant_id, name, brief_definition,
   inclusion_criteria, exclusion_criteria, examples, theory,
   definition_vector, probe_vector)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (code_id, codebook_version_id, tenant_id) DO NOTHING
"""


class CodebookEmbeddingError(CodebookError):
    """Raised when the embedder degraded to zero vectors during a load.

    Distinct from a plain CodebookError so a caller can tell "this codebook is
    malformed, fix it" from "the embedding service is down, retry".
    """


def _code_id(code: dict[str, Any]) -> Any:
    """The code's identifier, under either of the two accepted keys."""
    return code["code_id"] if "code_id" in code else code.get("id")


def _is_degraded(vector: str) -> bool:
    """True when a pgvector literal is all zeros (or empty).

    Every embedder in src/rag/embedder.py logs a warning and returns a zero
    vector rather than raising when its backend is unreachable, so an all-zero
    vector is the only signal that the embedding silently degraded. A code
    written with one can never pool against anything, so it must not be stored
    and reported as a successful load.
    """
    try:
        values = json.loads(vector)
    except (TypeError, ValueError):
        return False
    return isinstance(values, list) and not any(values)


async def _registered_code_ids(
    conn: asyncpg.Connection, version_id: UUID, tenant: str
) -> frozenset[str]:
    """The code_ids already written to code_registry under this codebook version."""
    rows = await conn.fetch(
        "SELECT code_id FROM code_registry WHERE codebook_version_id=$1 AND tenant_id=$2",
        version_id,
        tenant,
    )
    return frozenset(str(row["code_id"]) for row in rows)


async def _resolve_version(
    conn: asyncpg.Connection, doc: dict[str, Any], tenant: str
) -> tuple[UUID, frozenset[str]]:
    """Insert-or-find this codebook's version row, plus the code_ids already on it."""
    version_sha256 = codebook_sha256(doc)
    inserted = await conn.fetchval(
        """
        INSERT INTO codebook_versions (tenant_id, label, sha256, paradigm)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tenant_id, sha256) DO NOTHING
        RETURNING version_id
        """,
        tenant,
        f"v{version_sha256[:8]}",
        version_sha256,
        doc["paradigm"],
    )
    # Nothing inserted means this exact codebook is already loaded: reuse it.
    if inserted is not None:
        return UUID(str(inserted)), frozenset()

    existing = await conn.fetchval(
        "SELECT version_id FROM codebook_versions WHERE sha256=$1 AND tenant_id=$2",
        version_sha256,
        tenant,
    )
    if existing is None:
        raise CodebookError(
            f"Codebook version {version_sha256[:8]} could neither be inserted nor found"
        )
    version_id = UUID(str(existing))
    return version_id, await _registered_code_ids(conn, version_id, tenant)


async def _embed_codes(
    pending: list[dict[str, Any]], embed_fn: Callable[..., Awaitable[str]]
) -> list[tuple[dict[str, Any], str, str]]:
    """Embed each pending code (plain definition, instructed probe), no transaction open.

    Fails fast on the first degraded vector rather than collecting every failure:
    an unreachable embedder degrades every remaining code identically, so going on
    only buys one more connection timeout per code. Nothing is written, and the
    version row phase 1 created lets a re-submit resume the load once the
    embedding service is healthy.
    """
    embedded: list[tuple[dict[str, Any], str, str]] = []
    for code in pending:
        definition = code["brief_definition"]
        definition_vector = await embed_fn(definition, instructed=False)
        probe_vector = await embed_fn(definition, instructed=True)
        for column, vector in (
            ("definition_vector", definition_vector),
            ("probe_vector", probe_vector),
        ):
            if _is_degraded(vector):
                raise CodebookEmbeddingError(
                    f"Embedder degraded to a zero vector for {column} of code "
                    f"{_code_id(code)!r}; no codes were written. Retry once the "
                    "embedding service is reachable."
                )
        embedded.append((code, definition_vector, probe_vector))
    return embedded


async def _write_codes(
    conn: asyncpg.Connection,
    version_id: UUID,
    tenant: str,
    embedded: list[tuple[dict[str, Any], str, str]],
) -> None:
    """Write every embedded code to code_registry in one transaction."""
    for code, definition_vector, probe_vector in embedded:
        await conn.execute(
            _INSERT_CODE,
            _code_id(code),
            version_id,
            tenant,
            code.get("name"),
            code["brief_definition"],
            code.get("inclusion_criteria"),
            code.get("exclusion_criteria"),
            json.dumps(code.get("examples", [])),
            json.dumps(code.get("theory", {})),
            definition_vector,
            probe_vector,
        )


async def load_codebook(
    doc: dict[str, Any],
    pool: asyncpg.Pool,
    tenant: str,
    embed_fn: Callable[..., Awaitable[str]],
) -> UUID:
    """
    Load a codebook into the database, returning the version it resolved to.

    Validates the codebook, embeds each code (plain definition_vector,
    instructed probe_vector), and writes code_registry + codebook_versions.

    Runs in three phases against `pool`, each transaction short: resolve the
    version, embed with no transaction open, then write. The embed phase is a
    network round trip per code, and a pooled connection held
    idle-in-transaction across it would starve every concurrent caller.

    Loading is keyed on the codebook's content hash: a re-submit of an
    identical codebook resolves to the version_id the first load created
    (UNIQUE (tenant_id, sha256), migration 013) and re-embeds nothing that is
    already registered under it, so operator-calibrated pool_floor /
    residual_floor values on those rows survive untouched. Codes missing from
    that version (a load interrupted partway, or one the embedder failed) are
    still embedded and written, so an interrupted load can be resumed by
    re-submitting it.

    Args:
        doc: Validated codebook document
        pool: asyncpg pool (a bare connection will not do: the phases each open
              their own tenant-scoped transaction)
        tenant: Tenant UUID
        embed_fn: Async function(text, instructed: bool) -> pgvector literal
                 (typically via /api/embed endpoint)

    Returns:
        The codebook_versions.version_id this content hash maps to, whether
        this call created it or a previous load did.

    Raises:
        CodebookError: If validation fails, or if the version could neither be
            inserted nor found (the row was deleted concurrently).
        CodebookEmbeddingError: If the embedder degraded to a zero vector. No
            code_registry row is written.
    """
    validate_codebook(doc)

    # Phase 1 (resolve): claim or find the version, and learn what is already on it.
    async with tenant_connection(pool, tenant) as conn:
        version_id, registered = await _resolve_version(conn, doc, tenant)

    pending = [code for code in doc.get("categories", []) if _code_id(code) not in registered]

    # Phase 2 (embed): outside any transaction, since each embed is a round trip.
    embedded = await _embed_codes(pending, embed_fn)

    # Phase 3 (write): one short transaction over the vectors phase 2 produced.
    if embedded:
        async with tenant_connection(pool, tenant) as conn:
            await _write_codes(conn, version_id, tenant, embedded)

    return version_id
