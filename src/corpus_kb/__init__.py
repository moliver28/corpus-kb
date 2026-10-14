"""Corpus-KB: a local end-to-end RAG knowledge base with an MCP interface.

This package ``__init__`` is deliberately import-light: it re-exports nothing
from the package itself and exposes only the single-sourced version string.
Importing ``corpus_kb`` therefore costs one stdlib import at most and can
never pull in the config, storage, or server stack (no circular imports).

The version is single-sourced from the installed distribution metadata
(``pyproject.toml`` -> wheel METADATA -> ``importlib.metadata``), so
``pyproject.toml`` stays the one place a release version is written.
"""

from __future__ import annotations

# Fallback for imports that do not come from an installed distribution
# (a raw source checkout on ``sys.path``): a valid, clearly-unreleased
# version string instead of an import-time crash. Editable installs DO carry
# dist-info metadata, so they resolve the real version below.
_DEV_FALLBACK_VERSION = "0.0.0.dev0"

__all__ = ["__version__"]


def __getattr__(name: str) -> str:
    # PEP 562: resolve ``__version__`` lazily so importing the package never
    # pays for the metadata lookup until the version is actually needed.
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("corpus-kb")
        except PackageNotFoundError:
            return _DEV_FALLBACK_VERSION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
