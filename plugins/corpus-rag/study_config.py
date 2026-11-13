"""Plugin userConfig, the study environment, and the overlay (issue #351).

Claude Code prompts for ``userConfig`` keys when the plugin is enabled and
exports each one as ``CLAUDE_PLUGIN_OPTION_<KEY>``. This module reads them,
builds the per-study environment for engine runs, and resolves the config
overlay file. The plugin never parses YAML: it only passes the overlay path
through ``CORPUS_KB_CONFIG_OVERLAY``.

Stdlib only.
"""

from __future__ import annotations

import os
from pathlib import Path

from engine_paths import plugin_data_dir, state_dir

APP_DSN_TEMPLATE_DEFAULT = "postgresql://corpus_user:<password>@localhost:5432/{database}"
SUPERUSER_DSN_DEFAULT = "postgresql:///postgres"
BASE_PORT_DEFAULT = 8010
DEFAULT_BACKEND_DEFAULT = "postgres"

OVERLAY_FILENAME = "config.overlay.yaml"
LEGACY_OVERLAY_FILENAME = "config.overrides.yaml"


def option(key: str) -> str:
    """One userConfig value from the environment; empty when unset."""
    return os.environ.get(f"CLAUDE_PLUGIN_OPTION_{key.upper()}", "").strip()


def app_dsn_template() -> str:
    """The DSN template for one study database; ``{database}`` is replaced."""
    return option("app_dsn_template") or APP_DSN_TEMPLATE_DEFAULT


def superuser_dsn() -> str:
    """The superuser DSN for provision, migrate, and backup."""
    return option("superuser_dsn") or SUPERUSER_DSN_DEFAULT


def base_port() -> int:
    """The first port a study server may take."""
    raw = option("base_port")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return BASE_PORT_DEFAULT


def default_backend() -> str:
    """The storage backend new studies get: ``postgres`` or ``duckdb``."""
    return option("default_backend") or DEFAULT_BACKEND_DEFAULT


def study_dsn(database_name: str) -> str:
    """The app DSN for one study: the template with ``{database}`` filled."""
    return app_dsn_template().replace("{database}", database_name)


def socket_path(slug: str) -> Path:
    """One socket file per study, under the plugin data folder."""
    return plugin_data_dir() / "sockets" / f"{slug}.sock"


def overlay_path() -> Path | None:
    """The overlay file to pass through, or ``None``.

    The owner's legacy ``<state>/config.overrides.yaml`` wins when it
    exists, so their file keeps working. Otherwise the plugin overlay at
    ``<plugin data>/config.overlay.yaml`` is used when present. Neither is
    ever parsed here.
    """
    legacy = state_dir() / LEGACY_OVERLAY_FILENAME
    if legacy.is_file():
        return legacy
    ours = plugin_data_dir() / OVERLAY_FILENAME
    if ours.is_file():
        return ours
    return None


def study_env(slug: str, study: dict[str, object]) -> dict[str, str]:
    """The environment for one study's engine run (server, CLI, doctor).

    Sets the study database URL and backend, one socket path, and the
    overlay when one exists. The overlay passes through untouched.
    """
    env = dict(os.environ)
    env["CORPUS_KB_DATABASE_URL"] = str(study.get("database_url", ""))
    env["CORPUS_KB_DATABASE_BACKEND"] = str(study.get("backend", "postgres"))
    env["CORPUS_KB_SOCKET_PATH"] = str(socket_path(slug))
    overlay = overlay_path()
    if overlay is not None:
        env["CORPUS_KB_CONFIG_OVERLAY"] = str(overlay)
    return env
