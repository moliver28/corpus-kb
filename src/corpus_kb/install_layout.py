"""The pip command for an optional extra, honest for this install layout.

A wheel or pipx install lives in site-packages, so the clone-only
editable form (``pip install -e ".[extra]"``) cannot run there: there
is no checkout to build from. This module detects the layout through
the asset resolver (``_setup.setup_assets``: a packaged or
checkout-root ``pyproject.toml`` means a clone) and returns the command
that works for it. Checkouts keep the editable form; installed packages
get the package-name form (issue #464 follow-up, momus review of
PR #517).
"""

from __future__ import annotations

from corpus_kb._setup.setup_assets import resolve_setup_asset


def is_clone_layout() -> bool:
    """True when this install runs from a repository checkout."""
    return resolve_setup_asset("pyproject.toml") is not None


def extra_install_command(extra: str) -> str:
    """The pip command that installs ``extra`` in this layout."""
    if is_clone_layout():
        return f'pip install -e ".[{extra}]"'
    return f'pip install "corpus-kb[{extra}]"'


__all__ = ["extra_install_command", "is_clone_layout"]
