"""The one server.port reader for both transports."""

from __future__ import annotations

from typing import Any

from corpus_kb.errors import ConfigError


def coerce_server_port(server_cfg: dict[str, Any]) -> int:
    """Return the configured port as an int in range, or refuse with a remedy."""
    raw = server_cfg.get("port", 8010)
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ConfigError("Server port must be a whole number. Fix server.port in config.yaml.")
    try:
        port = int(raw)
    except ValueError:
        raise ConfigError(
            "Server port must be a whole number. Fix server.port in config.yaml."
        ) from None
    if not 1 <= port <= 65535:
        raise ConfigError(
            "Server port must be between 1 and 65535. Fix server.port in config.yaml."
        )
    return port
