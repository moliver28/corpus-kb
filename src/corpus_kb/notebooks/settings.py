"""Typed ``notebooks.*`` settings (UX program Wave 6).

Same house pattern as ``ui/settings.py`` (Wave 3) and
``topics/settings.py`` (Wave 4): the module owns the defaults, a frozen
dataclass carries the typed reads, and a loader validates so the YAML
files, ``config.py`` defaults and this module cannot drift. The defaults
merge into every ``config.load_config()`` result through
``config._apply_typed_module_defaults`` (NOTEBOOKS_CONFIG_DEFAULTS).

Keys and their contracts:

- ``enabled``: the feature flag. When true, ``server_wiring.startup``
  tries the lazy ``[notebook]`` extra mount; a missing extra or a
  missing directory is ONE honest log line and the server continues.
- ``directory``: the folder of marimo ``.py`` notebooks served at
  ``/notebooks``. Relative paths resolve against the process working
  directory; an absent folder is logged, never fatal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

DEFAULT_NOTEBOOKS_ENABLED = True
DEFAULT_NOTEBOOKS_DIRECTORY = "notebooks"

NOTEBOOKS_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": DEFAULT_NOTEBOOKS_ENABLED,
    "directory": DEFAULT_NOTEBOOKS_DIRECTORY,
}


@dataclass(frozen=True)
class NotebooksSettings:
    """Typed ``notebooks.*`` reads (UX program Wave 6)."""

    enabled: bool = DEFAULT_NOTEBOOKS_ENABLED
    directory: str = DEFAULT_NOTEBOOKS_DIRECTORY


def notebooks_settings_error(config: dict[str, object]) -> str | None:
    """Remediation text for invalid ``notebooks`` values, or None when valid.

    Only VALUES are rejected — a partial block (missing keys) is fine and
    fills from the defaults above. Called from ``config.load_config`` so
    a bad flag or folder fails AT LOAD, never mid-startup.
    """
    block = _notebooks_block(config)
    enabled = block.get("enabled", DEFAULT_NOTEBOOKS_ENABLED)
    if not isinstance(enabled, bool):
        return (
            "notebooks.enabled: must be a boolean "
            f"(true serves the notebooks console); got {enabled!r}"
        )
    directory = block.get("directory", DEFAULT_NOTEBOOKS_DIRECTORY)
    if not isinstance(directory, str) or not directory.strip():
        return (
            "notebooks.directory: must be a non-empty string "
            f"(the marimo notebooks folder); got {directory!r}"
        )
    return None


def load_notebooks_settings(config: dict[str, object]) -> NotebooksSettings:
    """Read ``notebooks.*`` with defaults; invalid values raise.

    Raises ``ValueError`` with the same remediation text load_config uses,
    so direct readers (tests, wiring) fail as loudly as a config load.
    """
    error = notebooks_settings_error(config)
    if error is not None:
        raise ValueError(error)
    block = _notebooks_block(config)
    return NotebooksSettings(
        enabled=bool(block.get("enabled", DEFAULT_NOTEBOOKS_ENABLED)),
        directory=str(block.get("directory", DEFAULT_NOTEBOOKS_DIRECTORY)),
    )


def _notebooks_block(config: dict[str, object]) -> dict[str, object]:
    """The ``notebooks`` mapping, or an empty one."""
    block = config.get("notebooks")
    if not isinstance(block, dict):
        return {}
    return cast(dict[str, object], block)
