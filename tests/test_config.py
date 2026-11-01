"""Tests for config loading and environment variable overrides."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pytest

from corpus_kb.config import (
    KNOWN_CORPUS_KB_ENV_VARS,
    _apply_typed_module_defaults,
    apply_extraction_backend_alias,
    load_config,
)


@pytest.fixture
def no_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every mapped CORPUS_KB_* env var before each test."""
    for var in KNOWN_CORPUS_KB_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_installer_defaults(no_env_overrides: None) -> None:
    """Installer block carries exactly the settings the installer reads."""
    cfg = load_config()
    installer = cast(dict[str, object], cfg["installer"])

    assert installer["postgres_image"] == "ghcr.io/moliver28/corpus-kb-postgres:0.1.0-pg17"
    assert installer["verify_image_signature"] is True
    assert "profiles" in installer
    assert "data_dir" in installer
    # Literal tilde form (issue #246): ~ expands at READ time, never at
    # import time, so the default stays machine-independent.
    assert installer["data_dir"] == "~/.corpus-kb"
    # Dead keys (zero-drift audit row 11) stay dead.
    for dead in ("auto_detect", "database_mode", "postgres_port", "ollama_mode", "telemetry"):
        assert dead not in installer


def test_installer_env_overrides_string_values(no_env_overrides: None) -> None:
    """String installer settings can be overridden via CORPUS_KB_INSTALL_* env vars."""
    env = {
        "CORPUS_KB_INSTALL_POSTGRES_IMAGE": "postgres:17",
        "CORPUS_KB_INSTALL_DATA_DIR": "/tmp/corpus-data",
    }
    with patch.dict("os.environ", env, clear=False):
        cfg = load_config()

    installer = cast(dict[str, object], cfg["installer"])
    assert installer["postgres_image"] == "postgres:17"
    assert installer["data_dir"] == "/tmp/corpus-data"


@pytest.mark.parametrize(
    ("var", "raw", "expected"),
    [
        ("CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE", "false", False),
        ("CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE", "0", False),
        ("CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE", "yes", True),
        ("CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE", "true", True),
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
    assert installer["verify_image_signature"] is expected


def test_int_env_overrides_parse_as_int(no_env_overrides: None) -> None:
    """CORPUS_KB_EMBEDDING_DIMENSIONS and CORPUS_KB_PORT parse as integers."""
    env = {
        "CORPUS_KB_EMBEDDING_DIMENSIONS": "4096",
        "CORPUS_KB_PORT": "9000",
    }
    with patch.dict("os.environ", env, clear=False):
        cfg = load_config()

    embedding = cast(dict[str, object], cfg["embedding"])
    server = cast(dict[str, object], cfg["server"])
    assert embedding["dimensions"] == 4096
    assert isinstance(embedding["dimensions"], int)
    assert server["port"] == 9000
    assert isinstance(server["port"], int)


def test_llm_timeout_env_override_parses_as_float(no_env_overrides: None) -> None:
    """CORPUS_KB_LLM_TIMEOUT_S parses as a float and lands in llm.timeout_s."""
    with patch.dict("os.environ", {"CORPUS_KB_LLM_TIMEOUT_S": "900.5"}, clear=False):
        cfg = load_config()

    llm = cast(dict[str, object], cfg["llm"])
    assert llm["timeout_s"] == 900.5
    assert isinstance(llm["timeout_s"], float)


def test_llm_timeout_default_absent(no_env_overrides: None) -> None:
    """Without the env var, llm.timeout_s keeps whatever the config files set."""
    cfg = load_config()

    llm = cast(dict[str, object], cfg["llm"])
    assert "timeout_s" not in llm or isinstance(llm["timeout_s"], (int, float))


def test_dead_env_mappings_are_ignored(no_env_overrides: None) -> None:
    """Removed mappings neither crash config load nor invent config keys.

    ``CORPUS_KB_STORAGE_PATH`` used to KeyError at load (no ``storage``
    section exists in the defaults and NOTHING read the key); the removed
    installer/graph mappings wrote keys with zero consumers.
    """
    dead_vars = {
        "CORPUS_KB_STORAGE_PATH": "/tmp/storage",
        "CORPUS_KB_GRAPH_PATH": "/tmp/graph.db",
        "CORPUS_KB_INSTALL_PROFILE": "performance",
        "CORPUS_KB_INSTALL_AUTO_DETECT": "false",
        "CORPUS_KB_INSTALL_DATABASE_MODE": "external",
        "CORPUS_KB_INSTALL_POSTGRES_PORT": "15432",
        "CORPUS_KB_INSTALL_OLLAMA_MODE": "container",
        "CORPUS_KB_INSTALL_TELEMETRY": "true",
    }
    with patch.dict("os.environ", dead_vars, clear=False):
        cfg = load_config()  # must not raise

    # storage.* is a CANONICAL section now (vector_type); the dead env var
    # must not have invented keys inside it.
    storage = cast(dict[str, object], cfg["storage"])
    assert "path" not in storage
    installer = cast(dict[str, object], cfg["installer"])
    assert "profile" not in installer


def test_dead_config_keys_absent(no_env_overrides: None) -> None:
    """Dead keys stay out of the loaded config (both defaults and YAML)."""
    cfg = load_config()
    server = cast(dict[str, object], cfg["server"])
    assert "name" not in server
    assert "chunking" not in cfg
    assert "llamaindex" not in cfg
    retrieval = cast(dict[str, object], cfg["retrieval"])
    assert "expand_context" not in retrieval
    assert "index_type" not in retrieval
    database = cast(dict[str, object], cfg["database"])
    assert "auto_migrate" not in database


def test_extractor_alias_backend_wins(no_env_overrides: None) -> None:
    """A set extraction.backend overrides graph.extractor (mirrored onto it)."""
    config: dict[str, object] = {
        "extraction": {"backend": "regex"},
        "graph": {"extractor": "langextract"},
    }
    apply_extraction_backend_alias(config)
    graph = cast(dict[str, object], config["graph"])
    assert graph["extractor"] == "regex"


def test_extractor_alias_legacy_only_kept(no_env_overrides: None) -> None:
    """Without extraction.backend, graph.extractor stays authoritative."""
    config: dict[str, object] = {"graph": {"extractor": "regex"}}
    apply_extraction_backend_alias(config)
    graph = cast(dict[str, object], config["graph"])
    assert graph["extractor"] == "regex"


def test_extractor_alias_survives_missing_sections() -> None:
    """The alias sync is safe on hand-built dicts without graph/extraction."""
    config: dict[str, object] = {}
    apply_extraction_backend_alias(config)
    assert "graph" not in config
    config = {"extraction": {"backend": "regex"}}
    apply_extraction_backend_alias(config)
    graph = cast(dict[str, object], config["graph"])
    assert graph["extractor"] == "regex"


def test_extractor_alias_applied_by_load_config(no_env_overrides: None) -> None:
    """load_config normalizes the shipped YAML: alias reflects the preferred key."""
    cfg = load_config()
    graph = cast(dict[str, object], cfg["graph"])
    extraction = cast(dict[str, object], cfg["extraction"])
    assert extraction["backend"] == "ml"
    assert graph["extractor"] == "ml"


def test_code_defaults_match_packaged_backend_and_data_dir() -> None:
    """Code-side defaults carry the packaged values (issue #246 lockstep).

    The loader deep-merges the packaged yaml OVER these defaults, so the
    runtime-effective value was already "ml"; the code side only stops
    disagreeing. duckdb code-fallback environments load again because the
    backend-mix gate refuses pgml AT LOAD.
    """
    from corpus_kb.config import get_default_config

    defaults = get_default_config()
    extraction = cast(dict[str, object], defaults["extraction"])
    graph = cast(dict[str, object], defaults["graph"])
    installer = cast(dict[str, object], defaults["installer"])
    assert extraction["backend"] == "ml"
    assert graph["extractor"] == "ml"
    assert installer["data_dir"] == "~/.corpus-kb"


def test_research_defaults_are_not_aliased_across_loads() -> None:
    """Mutating a loaded config's research block must not poison the defaults."""
    first: dict[str, object] = {}
    _apply_typed_module_defaults(first)
    first_research = cast(dict[str, object], first["research"])
    measurement = cast(dict[str, object], first_research["judgment_distribution"])
    measurement["use_mean"] = True
    measurement["injected"] = True

    second: dict[str, object] = {}
    _apply_typed_module_defaults(second)
    second_research = cast(dict[str, object], second["research"])
    fresh = cast(dict[str, object], second_research["judgment_distribution"])
    assert fresh == {"use_mean": False}


def test_nested_research_defaults_are_not_aliased_across_loads() -> None:
    """Deep nesting (release.profiles, three levels) must not alias either.

    load_config deep-merges user YAML into the defaults result, so a shared
    nested dict would absorb user overrides into the module constant for
    every later load in the process.
    """
    first: dict[str, object] = {}
    _apply_typed_module_defaults(first)
    first_research = cast(dict[str, object], first["research"])
    first_release = cast(dict[str, object], first_research["release"])
    first_profiles = cast(dict[str, object], first_release["profiles"])
    team = cast(dict[str, object], first_profiles["team-codebook"])
    gates = cast(list[object], team["required_gates"])
    gates.append("injected-gate")
    team["injected"] = True

    second: dict[str, object] = {}
    _apply_typed_module_defaults(second)
    second_research = cast(dict[str, object], second["research"])
    second_release = cast(dict[str, object], second_research["release"])
    second_profiles = cast(dict[str, object], second_release["profiles"])
    fresh = cast(dict[str, object], second_profiles["team-codebook"])
    assert "injected" not in fresh
    assert "injected-gate" not in cast(list[object], fresh["required_gates"])
