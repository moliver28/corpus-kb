"""Installer checkpoint state (zero-drift split of _setup/install.py).

Owns the resumable-setup persistence: the phase list, the state-file
location, and load/save/reset. ``install.py`` re-exports these so the
``install_module.*`` test/CLI surface stays unchanged.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path.home() / ".corpus-kb"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"

# Resumable installer phases. Order matters: each phase is idempotent and
# checkpoints to a state file on success so interrupted runs can resume.
INSTALL_PHASES: tuple[str, ...] = (
    "compose",
    "python",
    "database",
    "extensions",
    "models",
    "config",
)
INSTALL_STATE_VERSION = 1
INSTALL_STATE_FILE_NAME = "install_state.json"


def _install_state_dir(config: dict[str, Any]) -> Path:
    """Return the directory for installer checkpoint state files.

    Honors config.installer.data_dir when present, otherwise defaults to
    ~/.corpus-kb so the state is user-local and writable.
    """
    data_dir = config.get("installer", {}).get("data_dir")
    if data_dir:
        return Path(str(data_dir)).expanduser()
    return DEFAULT_CONFIG_DIR


def _install_state_path(config: dict[str, Any]) -> Path:
    """Return the path to the installer checkpoint state file."""
    return _install_state_dir(config) / INSTALL_STATE_FILE_NAME


def load_install_state(config: dict[str, Any]) -> dict[str, Any]:
    """Load persisted installer state, returning a fresh state if missing/invalid."""
    path = _install_state_path(config)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if isinstance(data, dict) and data.get("version") == INSTALL_STATE_VERSION:
            return data
    return {
        "version": INSTALL_STATE_VERSION,
        "completed": [],
        "last_run": None,
    }


def save_install_state(state: dict[str, Any], config: dict[str, Any]) -> None:
    """Persist installer checkpoint state, creating parent directories if needed."""
    path = _install_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.datetime.now(datetime.UTC).isoformat()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_install_state(config: dict[str, Any]) -> None:
    """Remove persisted installer state so the next run starts from phase 1."""
    path = _install_state_path(config)
    if path.exists():
        path.unlink()
