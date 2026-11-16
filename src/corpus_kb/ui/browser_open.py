"""Browser auto-open after the HTTP server binds (UI SPA program).

Extracted from ``server_wiring`` (size-ratchet F11): the auto-open
behavior is UI surface logic, so it lives beside the SPA mount it
depends on. ``server_wiring.run_all`` schedules the task; the guard and
the delay are policy here, not there.
"""

from __future__ import annotations

import asyncio
import os
import webbrowser
from typing import Any

# Set to "0" to keep the server from opening a browser tab (tests,
# headless hosts). Anything else — including unset — follows the /app/
# SPA mount outcome.
UI_OPEN_ENV_VAR = "CORPUS_KB_UI_OPEN"

UI_OPEN_DELAY_SECONDS = 1.5


def browser_open_enabled(environ: dict[str, str] | None = None) -> bool:
    """The auto-open guard: ``CORPUS_KB_UI_OPEN=0`` keeps the tab closed."""
    env = os.environ if environ is None else environ
    return env.get(UI_OPEN_ENV_VAR) != "0"


async def open_browser_when_up(host: str, port: int, delay: float = UI_OPEN_DELAY_SECONDS) -> None:
    """Open the browser once the server has had a moment to bind."""
    await asyncio.sleep(delay)
    webbrowser.open(f"http://{host}:{port}")


def schedule_browser_open(
    services: dict[str, Any],
    host: str,
    port: int,
) -> asyncio.Task[None] | None:
    """Schedule the delayed auto-open, or return None when it must not fire.

    Fires ONLY when the ``/app/`` SPA actually mounted (``ui_mounted``
    in the services dict) AND the env guard allows it. The browser opens
    the server root, which 307s to ``/app/``.
    """
    if not services.get("ui_mounted", False):
        return None
    if not browser_open_enabled():
        return None
    return asyncio.create_task(open_browser_when_up(host, port))


__all__ = [
    "UI_OPEN_DELAY_SECONDS",
    "UI_OPEN_ENV_VAR",
    "browser_open_enabled",
    "open_browser_when_up",
    "schedule_browser_open",
]
