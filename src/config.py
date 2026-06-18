"""Configuration loader — loads YAML config with environment variable overrides.

Priority:
1. Environment variables (CORPUS_KB_*)
2. Custom path (--config flag)
3. Default paths: ./config.yaml, ./corpus-kb/config.yaml, ~/.corpus-kb/config.yaml
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml


DEFAULT_PATHS = [
    Path.cwd() / "config.yaml",
    Path.cwd() / "corpus-kb" / "config.yaml",
    Path.home() / ".corpus-kb" / "config.yaml",
]


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    """Load config from a YAML file, merged with env var overrides."""
    # 1. Find config file
    config_path = None
    if path:
        config_path = Path(path)
    else:
        env_path = os.environ.get("CORPUS_KB_CONFIG")
        if env_path:
            config_path = Path(env_path)
        else:
            for p in DEFAULT_PATHS:
                if p.exists():
                    config_path = p
                    break

    # 2. Load YAML
    if config_path and config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    # 3. Environment variable overrides
    # CORPUS_KB_STORAGE_PATH -> config["storage"]["path"]
    # CORPUS_KB_EMBEDDING_MODEL -> config["embedding"]["model"]
    # CORPUS_KB_GRAPH_BACKEND -> config["graph"]["backend"]
    env_overrides = {
        ("storage", "path"): "CORPUS_KB_STORAGE_PATH",
        ("embedding", "model"): "CORPUS_KB_EMBEDDING_MODEL",
        ("embedding", "dimensions"): "CORPUS_KB_EMBEDDING_DIMENSIONS",
        ("graph", "backend"): "CORPUS_KB_GRAPH_BACKEND",
        ("graph", "db_path"): "CORPUS_KB_GRAPH_PATH",
        ("server", "transport"): "CORPUS_KB_TRANSPORT",
        ("server", "port"): "CORPUS_KB_PORT",
    }

    for (section, key), env_var in env_overrides.items():
        value = os.environ.get(env_var)
        if value is not None:
            if section not in config:
                config[section] = {}
            if key == "dimensions" or key == "port":
                config[section][key] = int(value)
            else:
                config[section][key] = value

    return config


def get_default_config() -> dict[str, Any]:
    """Return the default configuration dict (no file needed)."""
    return {
        "server": {
            "name": "corpus-kb",
            "transport": "stdio",
            "host": "localhost",
            "port": 8010,
        },
        "storage": {
            "path": str(Path.cwd() / "data" / "lancedb"),
        },
        "graph": {
            "backend": "sqlite",
            "db_path": str(Path.cwd() / "data" / "graph.db"),
        },
        "embedding": {
            "model": "nomic-embed-text",
            "dimensions": 768,
            "batch_size": 10,
        },
        "chunking": {
            "max_size": 2048,
            "overlap": 100,
        },
        "search": {
            "rrf_k": 60,
            "expand_context": False,
        },
    }
