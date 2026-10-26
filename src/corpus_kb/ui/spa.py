"""Mount the static lifecycle SPA at ``/app`` (UI SPA program Task 3).

Honest degradation is the contract (spec D.1, adapted per the plan): a
missing build logs ONE line and the server keeps serving the API. The
SPA is the only browser UI — there is no ``frontend`` choice (owner
ruling D9 closed 2026-10-25).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Vite builds ui_src/ into this sibling directory (vite.config.ts
# outDir; spec D.6). It ships as package data; a source checkout without
# a build simply degrades.
DIST_DIR = Path(__file__).resolve().parent.parent / "ui_dist"

SPA_MISSING_LOG = "[ui] SPA build not found - run: poe ui-build"


def spa_settings(config: dict[str, object]) -> dict[str, object]:
    """The runtime-tunable ``ui.spa`` block (never policy constants)."""
    ui = config.get("ui")
    if not isinstance(ui, dict):
        return {}
    spa = ui.get("spa")
    return dict(spa) if isinstance(spa, dict) else {}


def runtime_config_payload(config: dict[str, object]) -> dict[str, object]:
    """/app/runtime-config.json: the three spa keys plus the server version.

    The version handshake lets a stale SPA show its refresh banner
    (stale-SPA defense). The key list is closed: unknown ``ui.spa``
    entries never travel to the browser, and policy constants stay out
    by design.
    """
    from corpus_kb import __version__
    from corpus_kb.config import (
        SPA_MAX_UPLOAD_MB_DEFAULT,
        SPA_POLL_MS_DEFAULT,
        SPA_REQUEST_TIMEOUT_S_DEFAULT,
    )

    spa = spa_settings(config)
    return {
        "poll_ms": spa.get("poll_ms", SPA_POLL_MS_DEFAULT),
        "request_timeout_s": spa.get("request_timeout_s", SPA_REQUEST_TIMEOUT_S_DEFAULT),
        "max_upload_mb": spa.get("max_upload_mb", SPA_MAX_UPLOAD_MB_DEFAULT),
        "server_version": __version__,
    }


def mount_spa(app: FastAPI, config: dict[str, object], dist: Path = DIST_DIR) -> bool:
    """Serve the built SPA at ``/app``; True when mounted, never raises.

    Registration order is load-bearing (Review Focus 3): the JSON route
    and the no-cache index route are added BEFORE the ``/app`` mount so
    no later root mount can shadow them. Call this before any other UI
    mount in the wiring.
    """
    if not (dist / "index.html").is_file():
        logger.warning(SPA_MISSING_LOG)
        return False

    async def runtime_config(_: Request) -> Response:
        return JSONResponse(runtime_config_payload(config))

    async def index_html(_: Request) -> Response:
        # Always revalidate: a new deploy must reach every open tab.
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    async def to_spa(_: Request) -> Response:
        return RedirectResponse("/app/", status_code=307)

    app.add_route("/app/runtime-config.json", runtime_config, methods=["GET"])
    app.add_route("/app/", index_html, methods=["GET"])
    app.mount("/app", StaticFiles(directory=str(dist), html=True), name="spa")
    app.add_route("/", to_spa, methods=["GET"])
    return True
