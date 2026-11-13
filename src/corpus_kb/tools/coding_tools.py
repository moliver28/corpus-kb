"""The chunk-coding ops: ONE shared function per operation (issue #350).

Every surface routes through these functions — the MCP tools are
generated from the catalogue rows naming them, the socket spec arms
import them, the CLI commands call them, and the NEW routes (status,
export) call them like the statements routes do. The six pipeline ops
delegate to :class:`handlers.coding_handler.CodingHandler` (the ONE
implementation the existing HTTP routes already serve); status and
export read through ``handlers.coding_status_ops``.

The handler envelope passes through unchanged, so all four surfaces
answer byte-identically (the parity gate pins this).
"""

from __future__ import annotations

from typing import Any

from corpus_kb.errors import ValidationError
from corpus_kb.handlers.coding_handler import CodingHandler
from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = [
    "coding_calibrate_floors",
    "coding_code_batch",
    "coding_embed_codebook",
    "coding_export",
    "coding_pool",
    "coding_reliability",
    "coding_saturation",
    "coding_status",
]


def _handler(pg_pool: Any) -> CodingHandler:
    """One CodingHandler over the caller's storage seam (pool or gateway).

    The embedder resolves lazily inside the handler (the same
    ``OllamaEmbedder`` default the routes' singleton carries), so an MCP
    or CLI caller needs no wiring step.
    """
    return CodingHandler(pg_pool)


async def coding_embed_codebook(
    pg_pool: Any,
    codebook: dict[str, Any],
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Validate, embed, and load one codebook; answers its version id."""
    if not isinstance(codebook, dict):
        raise ValidationError("codebook must be a JSON object")
    load_config_or_pass(config)
    return await _handler(pg_pool).handle_embed_codebook(uuid_arg(tenant_id, "tenant_id"), codebook)


async def coding_pool(
    pg_pool: Any,
    codebook_version_id: str,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Run pooling (keyword hits, similarity) and coverage reconciliation."""
    load_config_or_pass(config)
    return await _handler(pg_pool).handle_pool(
        uuid_arg(tenant_id, "tenant_id"), uuid_arg(codebook_version_id, "codebook_version_id")
    )


async def coding_code_batch(
    pg_pool: Any,
    cells: list[dict[str, Any]],
    coder: str | None = None,
    model: str | None = None,
    batch_id: str | None = None,
    coding_pass: int = 1,
    bands: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Code a batch of (chunk, code) cells with confidence routing.

    ``bands`` rides as the raw threshold dict and is coerced to the
    handler's ``Bands`` here — the same coercion the HTTP route does.
    """
    from corpus_kb.coding.confidence_routing import Bands

    if not isinstance(cells, list):
        raise ValidationError("cells must be a JSON array")
    load_config_or_pass(config)
    return await _handler(pg_pool).handle_code_batch(
        uuid_arg(tenant_id, "tenant_id"),
        list(cells),
        coder=coder,
        model=model,
        batch_id=batch_id,
        coding_pass=coding_pass,
        bands=Bands(**bands) if bands else None,
    )


async def coding_reliability(
    pg_pool: Any,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    per_code_min: int = 5,
    seed: int = 17,
    code_universe: list[str] | None = None,
) -> dict[str, Any]:
    """Sample the coded corpus and compute alpha/kappa reliability."""
    load_config_or_pass(config)
    return await _handler(pg_pool).handle_reliability(
        uuid_arg(tenant_id, "tenant_id"),
        per_code_min=per_code_min,
        seed=seed,
        code_universe=code_universe,
    )


async def coding_calibrate_floors(
    pg_pool: Any,
    code_id: str,
    codebook_version_id: str,
    positive_chunk_ids: list[str] | None = None,
    negative_chunk_ids: list[str] | None = None,
    min_pos: int = 10,
    global_fallback: float | None = None,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Calibrate one code's pool/residual floors from labeled chunks."""
    load_config_or_pass(config)
    return await _handler(pg_pool).handle_calibrate_floors(
        uuid_arg(tenant_id, "tenant_id"),
        code_id=code_id,
        codebook_version_id=uuid_arg(codebook_version_id, "codebook_version_id"),
        positive_chunk_ids=[
            uuid_arg(str(c), "positive_chunk_ids") for c in (positive_chunk_ids or [])
        ],
        negative_chunk_ids=[
            uuid_arg(str(c), "negative_chunk_ids") for c in (negative_chunk_ids or [])
        ],
        min_pos=min_pos,
        global_fallback=global_fallback,
    )


async def coding_saturation(
    pg_pool: Any,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    batch_id: str | None = None,
    threshold: float = 0.05,
    min_samples: int = 50,
) -> dict[str, Any]:
    """Report the ISR saturation signal and the batch stop rule."""
    load_config_or_pass(config)
    return await _handler(pg_pool).handle_saturation(
        uuid_arg(tenant_id, "tenant_id"),
        batch_id=batch_id,
        threshold=threshold,
        min_samples=min_samples,
    )


async def coding_status(
    pg_pool: Any,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Corpus counts, saturation ratio, coverage by disposition, routes."""
    load_config_or_pass(config)
    uuid_arg(tenant_id, "tenant_id")
    from corpus_kb.handlers.coding_status_ops import coding_status as status_body

    return await status_body(pg_pool, tenant_id)


async def coding_export(
    pg_pool: Any,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = 1000,
    offset: int = 0,
) -> dict[str, Any]:
    """Accepted codes joined with chunk text and document source, as rows."""
    load_config_or_pass(config)
    uuid_arg(tenant_id, "tenant_id")
    from corpus_kb.handlers.coding_status_ops import coding_export as export_body

    return await export_body(pg_pool, tenant_id, limit=limit, offset=offset)
