"""Dead-config-key parity pins (parity program PR-7, gaps G05/G06/G21-G24/G29).

Five keys shipped validated-but-inert and are removed here; one key
(``retrieval.self_query.enabled``) was wired and its default flipped to
the safe off state; one key (``topics.zeroshot_min_similarity``) joined
the three-ways lockstep. Each test names the gap it pins.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml

from corpus_kb._setup.config_lockstep import flatten_leaves
from corpus_kb._setup.doctor_env import env_var_check, unknown_env_vars
from corpus_kb._setup.doctor_research import STATUS_WARN
from corpus_kb._setup.setup_steps import setup_update_config
from corpus_kb._setup.validate_configs_keys import RUNTIME_CONFIG_KEYS
from corpus_kb._setup.validate_configs_runtime import validate_runtime_config
from corpus_kb.config import KNOWN_CORPUS_KB_ENV_VARS, get_default_config
from corpus_kb.handlers import query_handler as query_handler_module
from corpus_kb.handlers.coding_handler import reset_coding_handler
from corpus_kb.handlers.command_handler import reset_command_handler
from corpus_kb.handlers.graph_handler import reset_graph_handler
from corpus_kb.handlers.idempotency import reset_idempotency_checker
from corpus_kb.handlers.router_handler import reset_router_handler
from corpus_kb.handlers.tag_handler import reset_tag_handler
from corpus_kb.handlers.versioning_handler import reset_versioning_handler
from corpus_kb.research.retrieval_settings import DEFAULT_SELF_QUERY, load_retrieval_settings
from corpus_kb.server_wiring import _wire_query_stack
from corpus_kb.topics.settings import TOPICS_CONFIG_DEFAULTS, load_topics_settings

REPO = Path(__file__).resolve().parents[1]
PACKAGED_YAML = REPO / "src" / "corpus_kb" / "config.yaml"

REMOVED_KEYS = (
    "retrieval.vector_index",
    "retrieval.fts_rebuild_debounce_s",
    "storage.vector_type",
    "retrieval.qa_context_in_fts",
    "server.transport",
)


def _offline_wiring_config(self_query_enabled: bool | None) -> dict[str, Any]:
    """An offline wiring config; the self-query flag is the only variable."""
    cfg: dict[str, Any] = {
        "database": {"connection_string": "postgresql://offline", "backend": "postgres"},
        "search": {"reranker": "none"},
        "embedding": {"provider": "ollama", "model": "offline-model", "dimensions": 8},
    }
    if self_query_enabled is not None:
        cfg["retrieval"] = {"self_query": {"enabled": self_query_enabled}}
    return cfg


@pytest.fixture
def _singletons_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot the query-handler singleton; reset the rest after each test."""
    monkeypatch.setattr(query_handler_module, "_query_handler", query_handler_module._query_handler)
    yield
    reset_router_handler()
    reset_tag_handler()
    reset_versioning_handler()
    reset_graph_handler()
    reset_coding_handler()
    reset_idempotency_checker()
    reset_command_handler()


@pytest.mark.asyncio
async def test_self_query_parser_builds_from_canonical_key(
    _singletons_restored: None,
) -> None:
    """G05: both wiring twins build the parser from retrieval.self_query."""
    from corpus_kb.server_wiring_duckdb import _wire_handlers as _duck_wire_handlers

    cfg = _offline_wiring_config(self_query_enabled=True)

    _embedder, pg_handler, _command = await _wire_query_stack(cfg, None)
    assert pg_handler._self_query_parser is not None

    from corpus_kb.storage.gateway import DuckDbGateway

    duck_cfg = dict(cfg)
    duck_cfg["database"] = {"backend": "duckdb"}
    gateway = await DuckDbGateway.connect(":memory:")
    try:
        duck_handlers = await _duck_wire_handlers(duck_cfg, gateway)
        assert duck_handlers.query_handler._self_query_parser is not None
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_self_query_default_off(_singletons_restored: None) -> None:
    """G05/R6: the shipped default keeps the parser off everywhere."""
    packaged = cast(dict[str, Any], yaml.safe_load(PACKAGED_YAML.read_text(encoding="utf-8")))
    defaults = cast(dict[str, Any], get_default_config())

    assert defaults["retrieval"]["self_query"]["enabled"] is False
    assert packaged["retrieval"]["self_query"]["enabled"] is False
    assert DEFAULT_SELF_QUERY["enabled"] is False

    _embedder, handler, _command = await _wire_query_stack(
        _offline_wiring_config(self_query_enabled=None), None
    )
    assert handler._self_query_parser is None


def test_removed_keys_absent_from_defaults() -> None:
    """G21/G22/G23/G06/G29: no removed key survives in defaults or yaml."""
    code_leaves = flatten_leaves(get_default_config())
    packaged = yaml.safe_load(PACKAGED_YAML.read_text(encoding="utf-8"))
    yaml_leaves = flatten_leaves(packaged)
    for dotted in REMOVED_KEYS:
        assert dotted not in code_leaves, dotted
        assert dotted not in yaml_leaves, dotted


def test_removed_keys_flagged_for_users() -> None:
    """G21-G23/G06/G29: the validator no longer requires the dead keys.

    The runtime validator is presence-plus-type over RUNTIME_CONFIG_KEYS;
    an unknown key is not an error. Removal means the keys leave the
    required set (a stale user config neither fails installs nor loads a
    value), and the validator modules carry no dead enum banks.
    """
    for dotted in REMOVED_KEYS:
        assert dotted not in RUNTIME_CONFIG_KEYS, dotted

    import corpus_kb._setup.validate_configs_keys as keys_module
    import corpus_kb._setup.validate_configs_runtime as runtime_module

    assert not hasattr(keys_module, "SUPPORTED_VECTOR_TYPES")
    assert not hasattr(keys_module, "SUPPORTED_VECTOR_INDEX_MODES")
    assert not hasattr(runtime_module, "SUPPORTED_VECTOR_TYPES")
    assert not hasattr(runtime_module, "SUPPORTED_VECTOR_INDEX_MODES")

    stale_user_config: dict[str, Any] = cast(
        dict[str, Any],
        yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8")),
    )
    stale_user_config["retrieval"]["vector_index"] = "hnsw"
    stale_user_config["retrieval"]["fts_rebuild_debounce_s"] = 2.0
    stale_user_config["retrieval"]["qa_context_in_fts"] = True
    stale_user_config["storage"] = {"vector_type": "halfvec"}
    stale_user_config["server"]["transport"] = "http"
    assert validate_runtime_config(stale_user_config) == []


def test_transport_env_var_doctor_warns() -> None:
    """G29/R5: CORPUS_KB_TRANSPORT left the known set, so doctor warns."""
    env = {"CORPUS_KB_TRANSPORT": "http"}
    assert "CORPUS_KB_TRANSPORT" not in KNOWN_CORPUS_KB_ENV_VARS
    assert unknown_env_vars(env) == ["CORPUS_KB_TRANSPORT"]
    check = env_var_check(env)
    assert check.status == STATUS_WARN
    assert "CORPUS_KB_TRANSPORT" in check.detail


def test_zeroshot_write_back_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G24/R3: setup write-back carries topics.zeroshot_min_similarity."""
    from corpus_kb._setup import setup_steps

    config_path = tmp_path / "config.yaml"
    config_dir = tmp_path / "confdir"
    monkeypatch.setattr(setup_steps, "DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr(setup_steps, "DEFAULT_CONFIG_DIR", config_dir)

    # Fresh write: the key ships with the defaults (the old drop is gone).
    assert setup_update_config(get_default_config(), "cpu", apply=True) == 0
    fresh = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert fresh["topics"]["zeroshot_min_similarity"] == 0.7

    # Update path: a user-set value survives the merge untouched.
    user_config: dict[str, Any] = {"topics": {"enabled": True, "zeroshot_min_similarity": 0.8}}
    config_path.write_text(yaml.safe_dump(user_config), encoding="utf-8")
    assert setup_update_config(get_default_config(), "cpu", apply=True) == 0
    merged = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert merged["topics"]["zeroshot_min_similarity"] == 0.8

    # The typed reader and the defaults bank agree with the shipped value.
    assert TOPICS_CONFIG_DEFAULTS["zeroshot_min_similarity"] == 0.7
    settings = load_topics_settings({"topics": {"zeroshot_min_similarity": 0.8}})
    assert settings.zeroshot_min_similarity == 0.8


def test_self_query_still_read_by_pipeline() -> None:
    """G05 regression: the query handler reads the canonical block only."""

    def _handler(enabled: bool) -> query_handler_module.QueryHandler:
        return query_handler_module.QueryHandler(
            SimpleNamespace(),  # pool: unused when a backend is given
            embedder=None,
            reranker=None,
            search_backend=SimpleNamespace(),
            config={"retrieval": {"self_query": {"enabled": enabled}}},
        )

    assert _handler(True)._self_query_enabled is True
    assert _handler(False)._self_query_enabled is False
    settings = load_retrieval_settings({"retrieval": {"self_query": {"enabled": True}}})
    assert settings.self_query["enabled"] is True
