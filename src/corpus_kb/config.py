"""Configuration loader — loads YAML config with environment variable overrides.

Priority (config discovery):
1. Explicit `path` argument
2. CORPUS_KB_CONFIG env var (absolute path) — set by MCP for config discovery
3. ./config.yaml (current working directory, dev mode)
4. Packaged default via importlib.resources (installed mode)

This allows corpus-kb to be called from any directory and still find its config,
which is critical for MCP integration where the working directory is unpredictable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

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


def load_config(path: str | None = None) -> dict[str, object]:
    """Load config from a YAML file, merged with defaults and env var overrides.

    Args:
        path: Optional explicit config file path

    Returns:
        Configuration dictionary with defaults and env var overrides applied

    Config discovery order:
        1. Explicit path argument
        2. CORPUS_KB_CONFIG environment variable
        3. ./config.yaml in the current working directory
        4. Packaged default config.yaml shipped with corpus_kb
    """
    config = get_default_config()

    config_path: Path | None = None
    if path:
        config_path = Path(path)
    else:
        env_path = os.environ.get("CORPUS_KB_CONFIG")
        if env_path:
            config_path = Path(env_path)

    if config_path is not None and config_path.exists():
        with config_path.open() as f:
            file_config = cast(dict[str, object], yaml.safe_load(f) or {})
        _deep_update(config, file_config)
    elif (Path.cwd() / "config.yaml").exists():
        cwd_config = Path.cwd() / "config.yaml"
        with cwd_config.open() as f:
            file_config = cast(dict[str, object], yaml.safe_load(f) or {})
        _deep_update(config, file_config)
    else:
        import importlib.resources as resources

        ref = resources.files("corpus_kb") / "config.yaml"
        with resources.as_file(ref) as packaged_path:
            with Path(packaged_path).open() as f:
                file_config = cast(dict[str, object], yaml.safe_load(f) or {})
            _deep_update(config, file_config)

    # Environment variable overrides
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
        ("installer", "auto_detect"): "CORPUS_KB_INSTALL_AUTO_DETECT",
        ("installer", "profile"): "CORPUS_KB_INSTALL_PROFILE",
        ("installer", "data_dir"): "CORPUS_KB_INSTALL_DATA_DIR",
        ("installer", "database_mode"): "CORPUS_KB_INSTALL_DATABASE_MODE",
        ("installer", "postgres_image"): "CORPUS_KB_INSTALL_POSTGRES_IMAGE",
        ("installer", "postgres_port"): "CORPUS_KB_INSTALL_POSTGRES_PORT",
        ("installer", "ollama_mode"): "CORPUS_KB_INSTALL_OLLAMA_MODE",
        ("installer", "telemetry"): "CORPUS_KB_INSTALL_TELEMETRY",
        ("installer", "verify_image_signature"): "CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE",
    }

    bool_keys = {"auto_detect", "telemetry", "verify_image_signature"}
    int_keys = {"dimensions", "port", "postgres_port"}

    for (section, key), env_var in env_overrides.items():
        value = os.environ.get(env_var)
        if value is not None:
            section_dict = cast(dict[str, object], config[section])
            if key in int_keys:
                section_dict[key] = int(value)
            elif key in bool_keys:
                section_dict[key] = value.lower() in {"1", "true", "yes", "on"}
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
        "graph": {
            "backend": "age",
            "extractor": "pgml",
            "model_version": "langextract-default",
            "prompt_version": "v1",
        },
        "embedding": {
            "provider": "pgml",
            "fallback_provider": "ollama",
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
            "index_type": "hnsw",
            "reranker": "none",
            "reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "rerank": {
                "enabled": True,
                "model": "qwen3-reranker:8b",
                "base_url": "http://localhost:11434",
                "over_retrieve_n": 60,
                "score_floor": 0.15,
                "calibration": "minmax",
            },
            "self_query": {
                "enabled": True,
                "model": "qwen3:4b",
                "base_url": "http://localhost:11434",
                "timeout_seconds": 8,
            },
            "matryoshka_enabled": False,
            "matryoshka_dim": 1024,
            "candidate_multiplier": 8,
            # U20: transaction-local HNSW scan settings, read by
            # corpus_kb.research.search_settings.load_hnsw_settings and applied
            # as SET LOCAL inside the retrieval transaction. Defaults mirror
            # HnswSettings(); keep the three files (config.py, config.yaml,
            # src/corpus_kb/config.yaml) byte-consistent on values.
            "hnsw": {
                "iterative_scan": "strict_order",
                "ef_search": 200,
                "max_scan_tuples": 20000,
            },
        },
        "contextual": {
            "enabled": False,
            "enabled_source_types": ["interview", "research"],
            "model": "qwen3:4b",
            "base_url": "http://localhost:11434",
            "temperature": 0.0,
            "fixture_dir": "tests/fixtures/contextual_recorded",
            "live_fallback": False,
        },
        "judge": {
            "enabled": True,
            "model": "qwen3:4b",
            "base_url": "http://localhost:11434",
            "max_claims": 20,
        },
        "routing": {
            "enabled": True,
            "confidence_threshold": 0.35,
            "prototypes": {
                "semantic": [
                    "what does the document say about X",
                    "explain the concept of X",
                    "summarize the section on X",
                ],
                "relational": [
                    "how many documents mention X",
                    "count the chunks by type",
                    "aggregate statistics across the corpus",
                ],
                "graph": [
                    "what is related to X",
                    "how is X connected to Y",
                    "what entities are linked to X",
                ],
            },
        },
        "llm": {
            "model": "qwen3:4b",
            "base_url": "http://localhost:11434",
        },
        "coding": {
            "coder": "qwen3:8b",
            "model": "qwen3:8b",
        },
        # research: MUST stay in lockstep with the packaged config.yaml block —
        # write_config's passthrough writes THIS dict into user configs, so a
        # missing block here would silently drop research.* keys (todo-19 (c)).
        "research": {
            "embedder": {
                # model_revision ("1024") and the G1-winning strategy
                # (naive-prefix) are FIXED IN CODE, deliberately not knobs:
                # embedding rows always record revision "1024" and the
                # strategy is a committed experiment result, not a runtime
                # choice. Only the promotion guard is configurable.
                "dimensions": 1024,
            },
            "ingest": {
                "watch_interval_s": 10,
            },
            "inductive": {
                "entropy_threshold": 0.85,
                "recluster_every_batches": 8,
                "centroid_drift_threshold": 0.15,
                "tau_dup": 0.85,
            },
            "cycle": {
                # codebook_promotion is HARD-FLOORED (never removable);
                # this list may only ADD halt gates.
                "halt_on": [
                    "interpretive_code_review",
                    "gray_zone_escalation",
                    "drift_alarm",
                    "overlap_conflict",
                    "threshold_unreliable",
                    "human_parity_breach",
                ],
            },
        },
        "database": {
            "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb",
        },
        "installer": {
            "auto_detect": True,
            "database_mode": "container",
            "postgres_image": "ghcr.io/moliver28/corpus-kb-postgres:0.1.0-pg17",
            "postgres_port": 5433,
            "ollama_mode": "external",
            "data_dir": str(Path.home() / ".corpus-kb"),
            "telemetry": False,
            "verify_image_signature": True,
            "profiles": {
                "minimal": {
                    "ram_gb_max": 8,
                    "vram_gb_max": 0,
                    "model": "nomic-embed-text",
                    "llm": "qwen3:0.6b",
                },
                "balanced": {
                    "ram_gb_max": 16,
                    "vram_gb_max": 4,
                    "model": "nomic-embed-text",
                    "llm": "qwen3:4b",
                },
                "performance": {
                    "ram_gb_max": 999,
                    "vram_gb_max": 8,
                    "model": "qwen3-embedding:8b-q8_0",
                    "llm": "qwen3:14b",
                },
            },
        },
    }
