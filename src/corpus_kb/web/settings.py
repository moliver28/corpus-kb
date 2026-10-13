"""WebFetchConfig construction from the runtime config ``web:`` section.

``ingest_url`` builds its fetch config from the loaded config dict so the
shipped YAML (pinned by validate_configs RUNTIME_CONFIG_KEYS) is the live
contract; code defaults apply only when a key is absent. A mistyped value
falls back to the default rather than crashing ingest — validate_configs is
the loud layer for type errors.
"""

from __future__ import annotations

from .fetch import WebFetchConfig

_DEFAULTS = WebFetchConfig()


def _as_str(raw: object, default: str) -> str:
    return raw if isinstance(raw, str) and raw else default


def _as_int(raw: object, default: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return default
    return raw


def _as_float(raw: object, default: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    return float(raw)


def _as_bool(raw: object, default: bool) -> bool:
    return raw if isinstance(raw, bool) else default


def config_from_mapping(mapping: object) -> WebFetchConfig:
    """Build a WebFetchConfig from a ``web:`` config section (or None)."""
    values: dict[str, object] = mapping if isinstance(mapping, dict) else {}
    return WebFetchConfig(
        user_agent=_as_str(values.get("user_agent"), _DEFAULTS.user_agent),
        timeout_seconds=_as_int(values.get("timeout_seconds"), _DEFAULTS.timeout_seconds),
        max_bytes=_as_int(values.get("max_bytes"), _DEFAULTS.max_bytes),
        respect_robots=_as_bool(values.get("respect_robots"), _DEFAULTS.respect_robots),
        max_pages=_as_int(values.get("max_pages"), _DEFAULTS.max_pages),
        max_depth=_as_int(values.get("max_depth"), _DEFAULTS.max_depth),
        delay_seconds=_as_float(values.get("delay_seconds"), _DEFAULTS.delay_seconds),
    )
