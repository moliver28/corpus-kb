"""Shared HTTP body-parsing helpers (thin-adapter plumbing).

One definition for every route module under ``api/``: the Starlette
adapters parse JSON, default the tenant, and map failures to an empty
body — nothing else. Kept dependency-light so both ``http.py`` and
``research_http.py`` import it without cycles.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


async def parse_body(request: Request) -> dict[str, Any]:
    """Parse the JSON body, returning an empty dict on failure."""
    try:
        data = await request.json()
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def tenant_id_of(body: dict[str, Any]) -> str:
    """The body's tenant_id or the shared placeholder default."""
    value = body.get("tenant_id", DEFAULT_TENANT_ID)
    return str(value) if value else DEFAULT_TENANT_ID
