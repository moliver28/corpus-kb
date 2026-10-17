"""Typed ``ui.*`` settings (UX program Wave 3).

Same house pattern as ``topics/settings.py`` (Wave 4): the module owns
the defaults, a frozen dataclass carries the typed reads, and a loader
validates so the YAML files, ``config.py`` defaults and this module
cannot drift. The defaults merge into every ``config.load_config()``
result through ``config._apply_typed_module_defaults``
(UI_CONFIG_DEFAULTS).

Keys and their contracts:

- ``enabled``: the feature flag. When true, ``server_wiring.startup``
  tries the lazy ``[ui]`` extra mount; a missing extra is ONE honest log
  line and the server continues without the browser UI. No other keys
  exist on purpose — the UI serves from the running HTTP port (see
  docs/UI.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

DEFAULT_UI_ENABLED = True

UI_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": DEFAULT_UI_ENABLED,
}


@dataclass(frozen=True)
class UiSettings:
    """Typed ``ui.*`` reads (UX program Wave 3)."""

    enabled: bool = DEFAULT_UI_ENABLED


def ui_settings_error(config: dict[str, object]) -> str | None:
    """Remediation text for invalid ``ui`` values, or None when valid.

    Only VALUES are rejected — a partial block (missing keys) is fine and
    fills from the defaults above. Called from ``config.load_config`` so
    a non-bool flag fails AT LOAD, never mid-startup.
    """
    block = _ui_block(config)
    enabled = block.get("enabled", DEFAULT_UI_ENABLED)
    if not isinstance(enabled, bool):
        return f"ui.enabled: must be a boolean (true serves the browser UI); got {enabled!r}"
    return None


def load_ui_settings(config: dict[str, object]) -> UiSettings:
    """Read ``ui.*`` with defaults; invalid values raise.

    Raises ``ValueError`` with the same remediation text load_config uses,
    so direct readers (tests, wiring) fail as loudly as a config load.
    """
    error = ui_settings_error(config)
    if error is not None:
        raise ValueError(error)
    block = _ui_block(config)
    return UiSettings(enabled=bool(block.get("enabled", DEFAULT_UI_ENABLED)))


def _ui_block(config: dict[str, object]) -> dict[str, object]:
    """The ``ui`` mapping, or an empty one."""
    block = config.get("ui")
    if not isinstance(block, dict):
        return {}
    return cast(dict[str, object], block)
