"""Resolve the Corpus-KB engine root and interpreter for this plugin.

The plugin runs engine commands through the ``corpus-kb`` CLI. This module
finds it. Resolution chain, first match wins:

1. ``CORPUS_KB_REPO`` environment variable, when it names a directory.
2. Dev checkout: the repository root two levels above this plugin folder
   (``plugins/corpus-rag`` inside a clone), when it holds a
   ``pyproject.toml`` for the ``corpus-kb`` project.
3. Engine install under the engine state dir: ``<state>/engine/``.
   State dir is ``CORPUS_KB_STATE_DIR``, else ``~/.corpus-kb``.

Return ``None`` when nothing resolves. Callers print one honest line and
point at ``docs/PLUGIN.md`` in the repository.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent

ENGINE_PROJECT_NAME = 'name = "corpus-kb"'


def state_dir() -> Path:
    """Engine state dir: ``CORPUS_KB_STATE_DIR`` or ``~/.corpus-kb``."""
    env = os.environ.get("CORPUS_KB_STATE_DIR", "")
    if env:
        return Path(env)
    return Path.home() / ".corpus-kb"


def engine_root(plugin_root: Path | None = None) -> Path | None:
    """Return the engine repository root, or ``None`` when not found."""
    root = plugin_root if plugin_root is not None else PLUGIN_ROOT

    env = os.environ.get("CORPUS_KB_REPO", "")
    if env and Path(env).is_dir():
        return Path(env)

    dev = root.parent.parent
    pyproject = dev / "pyproject.toml"
    if pyproject.is_file():
        try:
            if ENGINE_PROJECT_NAME in pyproject.read_text(encoding="utf-8", errors="replace"):
                return dev
        except OSError:
            pass

    installed = state_dir() / "engine"
    if installed.is_dir():
        return installed

    return None


def engine_cli(root: Path | None = None) -> str | None:
    """Return a ``corpus-kb`` command line, or ``None`` when not found.

    Prefers the engine checkout's virtualenv, then ``PATH``.
    """
    resolved = root if root is not None else engine_root()
    if resolved is not None:
        venv = resolved / ".venv"
        for candidate in (
            venv / "Scripts" / "corpus-kb.exe",
            venv / "Scripts" / "corpus-kb",
            venv / "bin" / "corpus-kb",
        ):
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("corpus-kb")
    return str(found) if found else None
