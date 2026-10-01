"""Tests for config loading and environment variable overrides."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from corpus_kb.config import load_config


@pytest.fixture
def no_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear installer-related env vars before each test."""
    for var in (
        "CORPUS_KB_INSTALL_AUTO_DETECT",
        "CORPUS_KB_INSTALL_PROFILE",
        "CORPUS_KB_INSTALL_DATA_DIR",
        "CORPUS_KB_INSTALL_DATABASE_MODE",
        "CORPUS_KB_INSTALL_POSTGRES_IMAGE",
        "CORPUS_KB_INSTALL_POSTGRES_PORT",
        "CORPUS_KB_INSTALL_OLLAMA_MODE",
        "CORPUS_KB_INSTALL_TELEMETRY",
        "CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE",
        "CORPUS_KB_STORAGE_PATH",
        "CORPUS_KB_EMBEDDING_MODEL",
        "CORPUS_KB_EMBEDDING_DIMENSIONS",
        "CORPUS_KB_GRAPH_BACKEND",
        "CORPUS_KB_GRAPH_PATH",
        "CORPUS_KB_TRANSPORT",
        "CORPUS_KB_PORT",
        "CORPUS_KB_DATABASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_installer_defaults(no_env_overrides: None) -> None:
    """Installer block carries the expected default settings."""
    cfg = load_config()
    installer = cast(dict[str, object], cfg["installer"])

    assert installer["auto_detect"] is True
    assert installer["database_mode"] == "container"
    assert installer["postgres_image"] == "ghcr.io/moliver28/corpus-kb-postgres:0.1.0-pg17"
    assert installer["postgres_port"] == 5433
    assert installer["ollama_mode"] == "external"
    assert installer["data_dir"] in {str(Path.home() / ".corpus-kb"), "~/.corpus-kb"}
    assert installer["telemetry"] is False
    assert installer["verify_image_signature"] is True
    assert "profiles" in installer


def test_installer_env_overrides_string_values(no_env_overrides: None) -> None:
    """String installer settings can be overridden via CORPUS_KB_INSTALL_* env vars."""
    env = {
        "CORPUS_KB_INSTALL_DATABASE_MODE": "external",
        "CORPUS_KB_INSTALL_POSTGRES_IMAGE": "postgres:17",
        "CORPUS_KB_INSTALL_OLLAMA_MODE": "container",
        "CORPUS_KB_INSTALL_DATA_DIR": "/tmp/corpus-data",
        "CORPUS_KB_INSTALL_PROFILE": "performance",
    }
    with patch.dict("os.environ", env, clear=False):
        cfg = load_config()

    installer = cast(dict[str, object], cfg["installer"])
    assert installer["database_mode"] == "external"
    assert installer["postgres_image"] == "postgres:17"
    assert installer["ollama_mode"] == "container"
    assert installer["data_dir"] == "/tmp/corpus-data"
    assert installer["profile"] == "performance"


def test_installer_env_overrides_int_value(no_env_overrides: None) -> None:
    """CORPUS_KB_INSTALL_POSTGRES_PORT is parsed as an integer."""
    with patch.dict("os.environ", {"CORPUS_KB_INSTALL_POSTGRES_PORT": "15432"}, clear=False):
        cfg = load_config()

    installer = cast(dict[str, object], cfg["installer"])
    assert installer["postgres_port"] == 15432
    assert isinstance(installer["postgres_port"], int)


@pytest.mark.parametrize(
    ("var", "raw", "expected"),
    [
        ("CORPUS_KB_INSTALL_AUTO_DETECT", "false", False),
        ("CORPUS_KB_INSTALL_AUTO_DETECT", "0", False),
        ("CORPUS_KB_INSTALL_AUTO_DETECT", "no", False),
        ("CORPUS_KB_INSTALL_AUTO_DETECT", "true", True),
        ("CORPUS_KB_INSTALL_AUTO_DETECT", "1", True),
        ("CORPUS_KB_INSTALL_TELEMETRY", "true", True),
        ("CORPUS_KB_INSTALL_TELEMETRY", "1", True),
        ("CORPUS_KB_INSTALL_TELEMETRY", "false", False),
        ("CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE", "false", False),
        ("CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE", "yes", True),
    ],
)
def test_installer_env_overrides_boolean_values(
    no_env_overrides: None,
    var: str,
    raw: str,
    expected: bool,
) -> None:
    """Boolean installer settings parse common truthy/falsy env var strings."""
    with patch.dict("os.environ", {var: raw}, clear=False):
        cfg = load_config()

    installer = cast(dict[str, object], cfg["installer"])
    key = var.replace("CORPUS_KB_INSTALL_", "").lower()
    assert installer[key] is expected


def test_installer_block_does_not_break_existing_overrides(no_env_overrides: None) -> None:
    """Non-installer env overrides still apply alongside installer defaults."""
    env = {
        "CORPUS_KB_DATABASE_URL": "postgresql://env:user@envhost:7777/envdb",
        "CORPUS_KB_EMBEDDING_MODEL": "env-model",
        "CORPUS_KB_PORT": "9000",
    }
    with patch.dict("os.environ", env, clear=False):
        cfg = load_config()

    database_cfg = cast(dict[str, object], cfg["database"])
    embedding_cfg = cast(dict[str, object], cfg["embedding"])
    server_cfg = cast(dict[str, object], cfg["server"])
    assert database_cfg["connection_string"] == "postgresql://env:user@envhost:7777/envdb"
    assert embedding_cfg["model"] == "env-model"
    assert server_cfg["port"] == 9000
    # Installer defaults are still present when not overridden.
    installer = cast(dict[str, object], cfg["installer"])
    assert installer["postgres_port"] == 5433
    assert installer["telemetry"] is False
