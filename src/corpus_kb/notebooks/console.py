"""The marimo ASGI mount (UX program Wave 6).

The ONLY module that imports marimo. Lazy-dependency contract (the
``ui/app.py`` pattern, from ``topics/engine.py``): stdlib-only module
imports, one import seam, and a typed ``MarimoDependencyError`` whose
remediation names the ``[notebook]`` extra. CI never installs the
extra; tests drive the seam with a ModuleNotFoundError and never
import marimo.

Known wart, documented on purpose: notebooks reload on server restart.
The console serves the configured directory dynamically, but a running
marimo server caches notebook modules until the process restarts.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

from corpus_kb.errors import DependencyMissingError

logger = logging.getLogger(__name__)

NOTEBOOK_EXTRA_REMEDIATION = "pip install 'corpus-kb[notebook]'"

# The mount point on the running HTTP app (one server, one port).
NOTEBOOKS_MOUNT_PATH = "/notebooks"


class MountableApp(Protocol):
    """The one ASGI-app surface the console needs (FastAPI or a test double)."""

    def mount(self, path: str, app: object) -> object: ...


class MarimoDependencyError(DependencyMissingError):
    """The ``[notebook]`` extra is not installed."""

    remediation = NOTEBOOK_EXTRA_REMEDIATION


def _import_module(name: str) -> ModuleType:
    """The one import seam (tests substitute a ModuleNotFoundError here)."""
    return importlib.import_module(name)


def load_marimo() -> ModuleType:
    """Import marimo lazily; a missing extra is a typed error."""
    try:
        return _import_module("marimo")
    except ModuleNotFoundError as exc:
        raise MarimoDependencyError(
            "The notebooks console needs the marimo package (the [notebook] extra)."
        ) from exc


def mount_notebooks(app: object, directory: str | Path) -> bool:
    """Mount the notebooks console at ``/notebooks``. NEVER raises.

    Returns True only when marimo loaded and the mount succeeded. A
    missing ``[notebook]`` extra or an absent directory logs ONE honest
    line and returns False — a broken console must not kill the server.
    """
    path = Path(directory)
    if not path.is_dir():
        logger.warning(
            "Notebooks directory %s not found — notebooks console disabled. "
            "Point notebooks.directory at a folder of marimo .py notebooks.",
            path,
        )
        return False
    try:
        marimo = load_marimo()
    except MarimoDependencyError:
        logger.info(
            "[notebook] extra not installed — notebooks console disabled. %s",
            NOTEBOOK_EXTRA_REMEDIATION,
        )
        return False
    except Exception as exc:
        logger.warning("Notebooks console failed to load (%s); continuing without it.", exc)
        return False
    try:
        asgi_app = _build_asgi_app(marimo, path)
        cast("MountableApp", app).mount(NOTEBOOKS_MOUNT_PATH, asgi_app)
    except Exception as exc:
        logger.warning("Notebooks console failed to mount (%s); continuing without it.", exc)
        return False
    logger.info(
        "Notebooks console mounted at %s (directory: %s). Notebooks reload on server restart.",
        NOTEBOOKS_MOUNT_PATH,
        path,
    )
    return True


def _build_asgi_app(marimo: ModuleType, directory: Path) -> object:
    """The dynamic-directory ASGI app (one marimo server, many notebooks).

    marimo's attributes resolve through ModuleType (the package is an
    optional extra with no import-time presence), so the builder chain
    is untyped by necessity; the calls mirror marimo's documented API.
    """
    builder = marimo.create_asgi_app()
    # marimo >= 0.25 takes a keyword-only mount path. Passing the outer mount
    # path keeps the dynamic middleware's base_path in sync with the FastAPI
    # app.mount(NOTEBOOKS_MOUNT_PATH, ...) below (same URL layout as the old
    # positional-directory API; marimo handles both stripped and kept prefixes).
    return builder.with_dynamic_directory(
        path=NOTEBOOKS_MOUNT_PATH, directory=str(directory)
    ).build()
