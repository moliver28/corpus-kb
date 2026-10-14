"""OpenAPI 3.1 export of the HTTP surface, generated from the catalogue.

``corpus-kb operations --openapi`` emits this document. There is no second
hand-written spec: the catalogue rows are the only input, so the exported
API truth cannot drift from the parity-tested truth.

Minimal but valid: paths, methods, request parameters (query for GET,
request body for POST), and response envelopes referenced from
``components/schemas``.
"""

from __future__ import annotations

import re
from typing import Any

from corpus_kb.contracts.model import HTTP, Operation, ParamSpec

_OPENAPI_VERSION = "3.1.0"
_ENVELOPE_REF = {"$ref": "#/components/schemas/SuccessEnvelope"}
_ERROR_REF = {"$ref": "#/components/schemas/ErrorEnvelope"}

_SUCCESS_ENVELOPE_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {
            "type": "string",
            "description": (
                "Operation outcome marker. Success variants are documented "
                "per operation family; anything else names the partial state."
            ),
        }
    },
    "additionalProperties": True,
}

_ERROR_ENVELOPE_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string", "const": "error"},
        "error": {"type": "string", "description": "Human-readable failure reason."},
        "error_type": {"type": "string", "description": "Error taxonomy class name."},
        "message": {"type": "string", "description": "Alternative plain failure text."},
    },
    "additionalProperties": True,
}


def _json_schema_type(param: ParamSpec) -> dict[str, Any]:
    """A minimal JSON Schema for one parameter (string for required bare)."""
    if param.required:
        schema: dict[str, Any] = {"type": "string"}
        if param.name.endswith("_id"):
            schema["format"] = "uuid"
        return schema
    default = param.default
    if isinstance(default, bool):
        return {"type": "boolean"}
    if isinstance(default, int):
        return {"type": "integer"}
    if isinstance(default, float):
        return {"type": "number"}
    if isinstance(default, str):
        return {"type": "string", "default": default}
    return {}  # no default (None) and optional: any JSON value is accepted


def _path_parameters(route: str) -> list[dict[str, Any]]:
    """One uuid path parameter per ``{placeholder}`` in the route template.

    A path template is only valid when every placeholder is declared. All
    current placeholders are document ids, so the schema is a uuid string.
    """
    return [
        {
            "name": name,
            "in": "path",
            "required": True,
            "schema": {"type": "string", "format": "uuid"},
        }
        for name in re.findall(r"\{([^{}]+)\}", route)
    ]


def _operation_object(op: Operation, method: str, route: str) -> dict[str, Any]:
    """One OpenAPI operation object for one catalogue row."""
    obj: dict[str, Any] = {
        "operationId": op.name,
        "summary": op.description,
        "responses": {
            "200": {
                "description": "Operation envelope",
                "content": {"application/json": {"schema": _ENVELOPE_REF}},
            },
            "default": {
                "description": "Error envelope",
                "content": {"application/json": {"schema": _ERROR_REF}},
            },
        },
    }
    path_params = _path_parameters(route)
    if path_params:
        obj["parameters"] = path_params
    params = [p for p in op.params if p.name != "tenant_id"]
    tenant = [p for p in op.params if p.name == "tenant_id"]
    if method == "GET":
        obj["parameters"] = [
            {
                "name": p.name,
                "in": "query",
                "required": p.required,
                "schema": _json_schema_type(p),
            }
            for p in (*params, *tenant)
        ]
    else:
        body_props = {p.name: _json_schema_type(p) for p in params}
        required = [p.name for p in params if p.required]
        body: dict[str, Any] = {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": body_props,
                        "additionalProperties": True,
                    }
                }
            }
        }
        if tenant:
            body["content"]["application/json"]["schema"]["properties"]["tenant_id"] = (
                _json_schema_type(tenant[0])
            )
        if required:
            body["content"]["application/json"]["schema"]["required"] = required
        obj["requestBody"] = body
    return obj


def build_openapi(operations: tuple[Operation, ...]) -> dict[str, Any]:
    """Build the OpenAPI 3.1 document for the catalogue's HTTP surface."""
    paths: dict[str, Any] = {}
    for op in operations:
        for surface in op.surfaces:
            if surface.kind != HTTP or surface.method is None:
                continue
            item = paths.setdefault(surface.route, {})
            item[surface.method.lower()] = _operation_object(op, surface.method, surface.route)
    return {
        "openapi": _OPENAPI_VERSION,
        "info": {
            "title": "Corpus-KB HTTP API",
            "version": "0.1.0",
            "description": (
                "Generated from the operation catalogue "
                "(corpus_kb.contracts). One operation, one handler, one "
                "envelope; HTTP is one of its surfaces."
            ),
        },
        "servers": [{"url": "http://127.0.0.1:8010"}],
        "paths": paths,
        "components": {
            "schemas": {
                "SuccessEnvelope": _SUCCESS_ENVELOPE_SCHEMA,
                "ErrorEnvelope": _ERROR_ENVELOPE_SCHEMA,
            }
        },
    }
