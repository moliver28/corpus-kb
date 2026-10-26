"""The lifecycle browser UI (UI SPA program).

The React SPA in ``ui_src/`` builds into ``ui_dist/`` and mounts at
``/app/`` through :func:`corpus_kb.ui.spa.mount_spa`. A missing build
logs one honest line; the API keeps working. This package holds only
the mount — the SPA holds no Python-importable logic.
"""

from __future__ import annotations
