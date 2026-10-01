"""Drift-guard tests for packaged default resources."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

from corpus_kb.config import load_config


def test_packaged_config_matches_root_config() -> None:
    """Packaged ``src/corpus_kb/config.yaml`` must stay in sync with root ``config.yaml``."""
    root = Path(__file__).parent.parent / "config.yaml"
    packaged = Path(__file__).parent.parent / "src" / "corpus_kb" / "config.yaml"

    root_config = yaml.safe_load(root.read_text(encoding="utf-8"))
    packaged_config = yaml.safe_load(packaged.read_text(encoding="utf-8"))

    assert root_config == packaged_config, (
        f"Packaged config ({packaged}) drifted from root config ({root}). "
        "Run: cp config.yaml src/corpus_kb/config.yaml"
    )


def test_load_config_resolves_from_cwd_in_dev_mode() -> None:
    """When run from the repo root, ``load_config()`` finds ``./config.yaml``."""
    cfg = load_config()
    server_cfg = cast(dict[str, object], cfg["server"])
    assert server_cfg["name"] == "corpus-kb"
    assert "installer" in cfg
    installer_cfg = cast(dict[str, object], cfg["installer"])
    assert installer_cfg["auto_detect"] is True
