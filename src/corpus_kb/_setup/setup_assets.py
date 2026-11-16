"""Locate the non-Python files the setup path reads (install-ease W2).

Setup reads the docker compose files, which are not Python code. A
wheel or pipx install ships them inside the ``corpus_kb`` package as
package data. A repository clone keeps them at the checkout root. This
module resolves each file in both layouts, the same way ``config.py``
resolves the default config: package data first, then the checkout
fallback.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

COMPOSE_FILE = "compose.yaml"
COMPOSE_BUILD_LOCAL_FILE = "compose.build-local.yaml"

# src/corpus_kb/_setup/setup_assets.py -> parents: [_setup, corpus_kb, src, root].
_CHECKOUT_ROOT = Path(__file__).resolve().parents[3]


def _packaged_asset(name: str) -> Path | None:
    """The asset copy inside the installed package, or None.

    Wheels and editable installs are filesystem layouts, so the
    traversable path is a real file path. Zip imports cannot serve
    ``docker compose -f`` a usable path and stay unsupported.
    """
    ref = importlib.resources.files("corpus_kb").joinpath(name)
    if not ref.is_file():
        return None
    return Path(str(ref))


def _repo_root() -> Path:
    """The repository checkout root (clone and editable layouts)."""
    return _CHECKOUT_ROOT


def resolve_setup_asset(name: str) -> Path | None:
    """Locate one non-Python setup file; None when absent everywhere.

    The packaged copy wins: wheel installs ship the file inside the
    package. The checkout root is the fallback for repository clones.
    """
    packaged = _packaged_asset(name)
    if packaged is not None:
        return packaged
    from_checkout = _repo_root() / name
    if from_checkout.is_file():
        return from_checkout
    return None


__all__ = [
    "COMPOSE_BUILD_LOCAL_FILE",
    "COMPOSE_FILE",
    "resolve_setup_asset",
]
