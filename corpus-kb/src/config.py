"""Configuration loader — loads YAML config with environment variable overrides.

Priority (config discovery):
1. CORPUS_KB_CONFIG env var (absolute path) — set by MCP for config discovery
2. --config flag (custom path)
3. Standard search paths (in order):
   - ./config.yaml (current working directory)
   - ./corpus-kb/config.yaml (project subdirectory)
   - ~/.corpus-kb/config.yaml (user home directory)
   - /opt/corpus-kb/config.yaml (system-wide installation)

This allows corpus-kb to be called from any directory and still find its config,
which is critical for MCP integration where the working directory is unpredictable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, cast

import yaml


def _deep_update(base: dict[str, object], overlay: dict[str, object]) -> None:
    """Recursively overlay values onto a base dictionary (mutating base)."""
    for key, value in overlay.items():
        base_value = base.get(key)
        if isinstance(value, dict) and isinstance(base_value, dict):
            _deep_update(
                cast(dict[str, object], base_value),
                cast(dict[str, object], value),
            )
        else:
            base[key] = value


DEFAULT_PATHS = [
    Path.cwd() / "config.yaml",
    Path.cwd() / "corpus-kb" / "config.yaml",
    Path.home() / ".corpus-kb" / "config.yaml",
    Path("/opt/corpus-kb/config.yaml"),  # System-wide installation
]


def load_config(path: Optional[str] = None) -> dict[str, object]:
    """Load config from a YAML file, merged with defaults and env var overrides.

    Args:
        path: Optional explicit config file path

    Returns:
        Configuration dictionary with defaults and env var overrides applied

    Config discovery order:
        1. Explicit path argument
        2. CORPUS_KB_CONFIG environment variable
        3. Standard search paths (cwd, ~/.corpus-kb, /opt/corpus-kb)
    """
    # 1. Find config file
    config_path: Optional[Path] = None
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

    # 2. Start with defaults, then overlay file config
    config = get_default_config()
    if config_path and config_path.exists():
        with open(config_path) as f:
            file_config = cast(dict[str, object], yaml.safe_load(f) or {})
        _deep_update(config, file_config)

    # 3. Environment variable overrides
    # CORPUS_KB_STORAGE_PATH -> config["storage"]["path"]
    # CORPUS_KB_EMBEDDING_MODEL -> config["embedding"]["model"]
    # CORPUS_KB_GRAPH_BACKEND -> config["graph"]["backend"]
    env_overrides: dict[tuple[str, str], str] = {
        ("storage", "path"): "CORPUS_KB_STORAGE_PATH",
        ("embedding", "model"): "CORPUS_KB_EMBEDDING_MODEL",
        ("embedding", "dimensions"): "CORPUS_KB_EMBEDDING_DIMENSIONS",
        ("graph", "backend"): "CORPUS_KB_GRAPH_BACKEND",
        ("graph", "db_path"): "CORPUS_KB_GRAPH_PATH",
        ("server", "transport"): "CORPUS_KB_TRANSPORT",
        ("server", "port"): "CORPUS_KB_PORT",
        ("database", "connection_string"): "CORPUS_KB_DATABASE_URL",
    }

    for (section, key), env_var in env_overrides.items():
        value = os.environ.get(env_var)
        if value is not None:
            section_dict = cast(dict[str, object], config[section])
            if key == "dimensions" or key == "port":
                section_dict[key] = int(value)
            else:
                section_dict[key] = value

    return config


def get_default_config() -> dict[str, object]:
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
            "lancedb_uri": "./data/lancedb",
            "graph_db": "./data/graph.db",
        },
        "graph": {
            "backend": "sqlite",
            "db_path": str(Path.cwd() / "data" / "graph.db"),
            "extractor": "langextract",
        },
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "base_url": "http://localhost:11434",
            "batch_size": 32,
            "dimensions": 768,
        },
        "chunking": {
            "max_size": 4096,
            "overlap": 200,
        },
        "search": {
            "rrf_k": 60,
            "expand_context": True,
        },
        "database": {
            "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb",
        },
    }
