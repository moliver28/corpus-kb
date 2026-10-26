"""Demo route family (UI SPA program Task 5a).

POST /api/demo/load — load the bundled sample knowledge corpus
(``docs/demo-kb``) through the standard ingest tool layer.

Thin adapter: the ONE handler is ``demo_kb.load_demo_kb`` (the same entry
the ``corpus-kb demo-kb`` CLI command calls), so the demo cannot drift
from the real pipeline. The call is idempotent: files already ingested
come back as ``skipped``. A missing corpus folder raises the typed
``DemoCorpusMissingError`` (a ``DependencyMissingError``), which
``@route_errors`` answers with 503 — never a bare 500.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import route_errors


@route_errors
async def demo_load(request: Request) -> JSONResponse:
    """POST /api/demo/load — ingest the bundled demo corpus; house receipt."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.demo_kb import load_demo_kb

    result = await load_demo_kb(None, await research_pool())
    return JSONResponse(result)
