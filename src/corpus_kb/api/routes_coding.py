"""Coding route family (zero-drift split of api/http.py).

POST /api/coding/embed-codebook, /api/coding/pool, /api/coding/code-batch
POST /api/coding/reliability, /api/coding/calibrate-floors, /api/coding/saturation

Thin adapters: parse JSON, call the shared coding handler, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests can
monkeypatch the handler-module singletons.
"""

from __future__ import annotations

from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import opt_float, opt_int, parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID


@route_errors
async def coding_embed_codebook(request: Request) -> JSONResponse:
    """POST /api/coding/embed-codebook — Load codebook and embed each code."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    body = await parse_body(request)
    tenant_id = UUID(body.get("tenant_id", DEFAULT_TENANT_ID))
    codebook = body.get("codebook") or {}
    result = await get_coding_handler().handle_embed_codebook(tenant_id, codebook)
    return JSONResponse(result)


@route_errors
async def coding_pool(request: Request) -> JSONResponse:
    """POST /api/coding/pool — Run pooling (keyword + similarity)."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    body = await parse_body(request)
    tenant_id = UUID(body.get("tenant_id", DEFAULT_TENANT_ID))
    codebook_version_id = UUID(str(body["codebook_version_id"]))
    result = await get_coding_handler().handle_pool(tenant_id, codebook_version_id)
    return JSONResponse(result)


@route_errors
async def coding_code_batch(request: Request) -> JSONResponse:
    """POST /api/coding/code-batch — Run batch coding with routing."""
    from corpus_kb.coding.confidence_routing import Bands
    from corpus_kb.handlers.coding_handler import get_coding_handler

    body = await parse_body(request)
    tenant_id = UUID(body.get("tenant_id", DEFAULT_TENANT_ID))
    raw_bands = body.get("bands")
    bands = Bands(**raw_bands) if raw_bands else None
    result = await get_coding_handler().handle_code_batch(
        tenant_id,
        list(body.get("cells", [])),
        coder=body.get("coder"),
        model=body.get("model"),
        batch_id=body.get("batch_id"),
        coding_pass=opt_int(body.get("coding_pass"), 1, name="coding_pass"),
        bands=bands,
    )
    return JSONResponse(result)


@route_errors
async def coding_reliability(request: Request) -> JSONResponse:
    """POST /api/coding/reliability — Compute reliability (alpha/kappa)."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    body = await parse_body(request)
    tenant_id = UUID(body.get("tenant_id", DEFAULT_TENANT_ID))
    result = await get_coding_handler().handle_reliability(
        tenant_id,
        per_code_min=opt_int(body.get("per_code_min"), 5, name="per_code_min"),
        # Seeds are any-int (random.Random takes negatives); only the
        # database int8 bind range is refused.
        seed=opt_int(body.get("seed"), 17, name="seed", allow_negative=True),
        code_universe=body.get("code_universe"),
    )
    return JSONResponse(result)


@route_errors
async def coding_calibrate_floors(request: Request) -> JSONResponse:
    """POST /api/coding/calibrate-floors — Calibrate one code's pool/residual floor."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    body = await parse_body(request)
    tenant_id = UUID(body.get("tenant_id", DEFAULT_TENANT_ID))
    result = await get_coding_handler().handle_calibrate_floors(
        tenant_id,
        code_id=str(body["code_id"]),
        codebook_version_id=UUID(str(body["codebook_version_id"])),
        positive_chunk_ids=[UUID(str(c)) for c in body.get("positive_chunk_ids", [])],
        negative_chunk_ids=[UUID(str(c)) for c in body.get("negative_chunk_ids", [])],
        min_pos=opt_int(body.get("min_pos"), 10, name="min_pos"),
        global_fallback=body.get("global_fallback"),
    )
    return JSONResponse(result)


@route_errors
async def coding_saturation(request: Request) -> JSONResponse:
    """POST /api/coding/saturation — ISR + stop-rule saturation signal."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    body = await parse_body(request)
    tenant_id = UUID(body.get("tenant_id", DEFAULT_TENANT_ID))
    result = await get_coding_handler().handle_saturation(
        tenant_id,
        batch_id=body.get("batch_id"),
        threshold=opt_float(body.get("threshold"), 0.05, name="threshold"),
        min_samples=opt_int(body.get("min_samples"), 50, name="min_samples"),
    )
    return JSONResponse(result)
