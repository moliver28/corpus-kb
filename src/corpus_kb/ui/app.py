"""The NiceGUI mount seam (UX program Wave 3).

The ONLY module that imports NiceGUI. Lazy-dependency contract (the
``topics/engine.py`` pattern): stdlib-only module imports, one import
seam, a Protocol-pinned surface (``ui/types.py``), and a typed
``UiDependencyError`` whose remediation names the ``[ui]`` extra. CI
never installs the extra; extras-less tests drive the seam with a
ModuleNotFoundError and never import nicegui.

``build_ui`` mutates the running FastAPI app via ``ui.run_with`` — no
new daemon, no new port. Page registration lives in ``pages.py``.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import cast

from corpus_kb.errors import DependencyMissingError
from corpus_kb.ui.pages import register_pages
from corpus_kb.ui.types import NiceGuiModule

UI_EXTRA_REMEDIATION = "pip install 'corpus-kb[ui]'"


class UiDependencyError(DependencyMissingError):
    """The ``[ui]`` extra is not installed."""

    remediation = UI_EXTRA_REMEDIATION


def _import_module(name: str) -> ModuleType:
    """The one import seam (tests substitute a ModuleNotFoundError here)."""
    return importlib.import_module(name)


def load_nicegui() -> ModuleType:
    """Import nicegui lazily; a missing extra is a typed error."""
    try:
        return _import_module("nicegui")
    except ModuleNotFoundError as exc:
        raise UiDependencyError(
            "The browser UI needs the nicegui package (the [ui] extra)."
        ) from exc


def build_ui(app: object) -> None:
    """Mount the pages onto the running HTTP app (``ui.run_with``).

    Raises ``UiDependencyError`` when the ``[ui]`` extra is absent; the
    caller (server_wiring.mount_ui) turns that into ONE honest log line.
    """
    nicegui = cast(NiceGuiModule, load_nicegui())
    register_pages(nicegui.ui)
    nicegui.ui.run_with(app, title="Corpus-KB")
