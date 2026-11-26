"""Typed ``telemetry.*`` settings (issue #442, the opt-in event log).

Same house pattern as ``analysis/settings.py``: the module owns the
defaults, a frozen dataclass carries the typed read, and a loader
validates so the YAML files, ``config.py`` defaults and this module
cannot drift. The whole feature ships OFF; nothing here touches the
network and no event ever carries corpus content.

Keys and their contracts:

- ``telemetry.enabled``: the opt-in flag. Must be a real bool. Every
  code path runs green while it is ``false`` (the shipped default).
- ``telemetry.log_dir``: the folder for ``events.jsonl``. Must be a
  non-empty string. The CONFIG LEAF stays a portable string; resolution
  happens ONCE at load time (owner-confirm F18): an absolute value (or a
  ``~`` path after expansion) passes through unchanged; a relative value
  resolves under the installer data dir (``installer.data_dir``, read
  inline — importing ``_setup`` would break the layering contracts) —
  NEVER against the process working directory, NEVER at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

DEFAULT_TELEMETRY_ENABLED = False
DEFAULT_TELEMETRY_LOG_DIR = "telemetry_logs"
# The installer leaf default (config.py carries the same value); read
# inline below because storage-side modules must not import _setup.
DEFAULT_INSTALLER_DATA_DIR = "~/.corpus-kb"

@dataclass(frozen=True)
class TelemetrySettings:
    """Typed ``telemetry.*`` reads (issue #442)."""

    enabled: bool = DEFAULT_TELEMETRY_ENABLED
    log_dir: Path = Path(DEFAULT_TELEMETRY_LOG_DIR)


def telemetry_settings_error(config: dict[str, object]) -> str | None:
    """Remediation text for invalid telemetry values, or None when valid.

    Only VALUES are rejected — a partial block (missing keys) is fine and
    fills from the defaults above. Called from ``config.load_config`` so
    a bad value fails AT LOAD, never mid-run.
    """
    flag = _telemetry_block(config).get("enabled", DEFAULT_TELEMETRY_ENABLED)
    if not isinstance(flag, bool):
        return "telemetry.enabled must be true or false. Set it to a boolean value."
    log_dir = _telemetry_block(config).get("log_dir", DEFAULT_TELEMETRY_LOG_DIR)
    if not isinstance(log_dir, str) or not log_dir.strip():
        return "telemetry.log_dir must be a non-empty folder name or path."
    return None


def load_telemetry_settings(config: dict[str, object]) -> TelemetrySettings:
    """Read ``telemetry.*`` with defaults; invalid values raise.

    Raises ``ValueError`` with the same remediation text load_config
    uses, so direct readers (tests, adapters) fail as loudly as a config
    load. ``log_dir`` resolves once here (F18); the returned Path is
    already absolute for every relative input.
    """
    error = telemetry_settings_error(config)
    if error is not None:
        raise ValueError(error)
    block = _telemetry_block(config)
    enabled = bool(block.get("enabled", DEFAULT_TELEMETRY_ENABLED))
    raw_dir = str(block.get("log_dir", DEFAULT_TELEMETRY_LOG_DIR))
    return TelemetrySettings(enabled=enabled, log_dir=_resolve_log_dir(config, raw_dir))


def _resolve_log_dir(config: dict[str, object], raw_dir: str) -> Path:
    """Expand ``~``, pass absolute values through, park the rest under data_dir."""
    candidate = Path(raw_dir).expanduser()
    if candidate.is_absolute():
        return candidate
    data_dir = Path(_installer_data_dir(config)).expanduser()
    return data_dir / candidate


def _installer_data_dir(config: dict[str, object]) -> str:
    """The ``installer.data_dir`` leaf, read inline (no _setup import)."""
    installer = config.get("installer")
    if isinstance(installer, dict):
        value = cast("dict[str, object]", installer).get("data_dir")
        if isinstance(value, str) and value.strip():
            return value
    return DEFAULT_INSTALLER_DATA_DIR


def _telemetry_block(config: dict[str, object]) -> dict[str, object]:
    """The ``telemetry`` mapping, or an empty one."""
    block = config.get("telemetry")
    if not isinstance(block, dict):
        return {}
    return cast(dict[str, object], block)
