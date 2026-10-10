"""Tests for U20 transaction-local HNSW settings (search.hnsw.* config keys)."""

from __future__ import annotations

import pytest

from corpus_kb.research.search_settings import (
    DEFAULT_EF_SEARCH,
    DEFAULT_ITERATIVE_SCAN,
    DEFAULT_MAX_SCAN_TUPLES,
    HnswSettings,
    apply_hnsw_settings,
    load_hnsw_settings,
    parse_pgvector_version,
    set_local_statements,
    supports_iterative_scan,
)

VALID_ITERATIVE_SCAN_VALUES = {"off", "relaxed_order", "strict_order"}


def test_frozen_dataclass_defaults_match_brief() -> None:
    from dataclasses import FrozenInstanceError

    settings = HnswSettings()
    assert settings.iterative_scan == "strict_order"
    assert settings.ef_search == 200
    assert settings.max_scan_tuples == 20000
    with pytest.raises(FrozenInstanceError):
        settings.ef_search = 1


def test_default_constants_align_with_dataclass() -> None:
    assert DEFAULT_ITERATIVE_SCAN == "strict_order"
    assert DEFAULT_EF_SEARCH == 200
    assert DEFAULT_MAX_SCAN_TUPLES == 20000


def test_load_hnsw_settings_reads_search_hnsw_block() -> None:
    config = {
        "search": {
            "hnsw": {
                "iterative_scan": "relaxed_order",
                "ef_search": 400,
                "max_scan_tuples": 50000,
            }
        }
    }
    settings = load_hnsw_settings(config)
    assert settings == HnswSettings(
        iterative_scan="relaxed_order", ef_search=400, max_scan_tuples=50000
    )


def test_load_hnsw_settings_defaults_when_block_missing() -> None:
    assert load_hnsw_settings({}) == HnswSettings()
    assert load_hnsw_settings({"search": {}}) == HnswSettings()
    assert load_hnsw_settings({"search": {"hnsw": {}}}) == HnswSettings()


def test_load_hnsw_settings_partial_block_fills_defaults() -> None:
    settings = load_hnsw_settings({"search": {"hnsw": {"ef_search": 64}}})
    assert settings == HnswSettings(
        iterative_scan=DEFAULT_ITERATIVE_SCAN, ef_search=64, max_scan_tuples=20000
    )


def test_load_hnsw_settings_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="iterative_scan"):
        load_hnsw_settings({"search": {"hnsw": {"iterative_scan": "turbo"}}})


def test_load_hnsw_settings_rejects_nonpositive_limits() -> None:
    with pytest.raises(ValueError, match="ef_search"):
        load_hnsw_settings({"search": {"hnsw": {"ef_search": 0}}})
    with pytest.raises(ValueError, match="max_scan_tuples"):
        load_hnsw_settings({"search": {"hnsw": {"max_scan_tuples": -1}}})


def test_off_mode_emits_no_statements() -> None:
    assert set_local_statements(HnswSettings(iterative_scan="off")) == ()


def test_statements_are_valid_set_local_sql() -> None:
    statements = set_local_statements(HnswSettings())
    assert statements == (
        "SET LOCAL hnsw.iterative_scan = 'strict_order'",
        "SET LOCAL hnsw.ef_search = 200",
        "SET LOCAL hnsw.max_scan_tuples = 20000",
    )


def test_parse_pgvector_version() -> None:
    assert parse_pgvector_version("0.8.0") == (0, 8, 0)
    assert parse_pgvector_version("0.8.2") == (0, 8, 2)
    assert parse_pgvector_version("0.7.4") == (0, 7, 4)
    assert parse_pgvector_version("") is None
    assert parse_pgvector_version("junk") is None


def test_supports_iterative_scan_gate() -> None:
    assert supports_iterative_scan("0.8.0") is True
    assert supports_iterative_scan("0.8.3") is True
    assert supports_iterative_scan("0.7.4") is False
    # Unknown versions must NOT silently enable the feature.
    assert supports_iterative_scan(None) is False
    assert supports_iterative_scan("junk") is False


def test_apply_hnsw_settings_emits_inside_callers_transaction() -> None:
    import asyncio

    executed: list[tuple[str, tuple[object, ...]]] = []

    class _Conn:
        async def execute(self, sql: str, *args: object) -> str:
            executed.append((sql, args))
            return "OK"

    applied = asyncio.run(apply_hnsw_settings(_Conn(), HnswSettings()))
    assert applied is True
    assert [sql for sql, _ in executed] == list(set_local_statements(HnswSettings()))
    assert all(args == () for _, args in executed)


def test_apply_hnsw_settings_off_is_noop() -> None:
    import asyncio

    executed: list[str] = []

    class _Conn:
        async def execute(self, sql: str, *args: object) -> str:
            executed.append(sql)
            return "OK"

    applied = asyncio.run(apply_hnsw_settings(_Conn(), HnswSettings(iterative_scan="off")))
    assert applied is False
    assert executed == []


def test_default_config_ships_search_hnsw_block() -> None:
    from typing import cast

    from corpus_kb.config import get_default_config

    search = cast(dict[str, object], get_default_config()["search"])
    assert cast(dict[str, object], search["hnsw"]) == {
        "iterative_scan": DEFAULT_ITERATIVE_SCAN,
        "ef_search": DEFAULT_EF_SEARCH,
        "max_scan_tuples": DEFAULT_MAX_SCAN_TUPLES,
    }


def test_both_yaml_files_ship_search_hnsw_block() -> None:
    from pathlib import Path

    import yaml

    repo = Path(__file__).resolve().parent.parent
    for rel in ("config.yaml", Path("src") / "corpus_kb" / "config.yaml"):
        data = yaml.safe_load((repo / rel).read_text(encoding="utf-8"))
        hnsw = (data.get("search") or {}).get("hnsw")
        assert hnsw == {
            "iterative_scan": DEFAULT_ITERATIVE_SCAN,
            "ef_search": DEFAULT_EF_SEARCH,
            "max_scan_tuples": DEFAULT_MAX_SCAN_TUPLES,
        }, f"{rel} search.hnsw block missing or inconsistent"


def test_load_hnsw_settings_matches_default_config() -> None:
    from corpus_kb.config import get_default_config

    assert load_hnsw_settings(get_default_config()) == HnswSettings()


def test_iterative_scan_values_are_pgvector_modes() -> None:
    from corpus_kb.research.search_settings import ITERATIVE_SCAN_MODES

    assert ITERATIVE_SCAN_MODES == VALID_ITERATIVE_SCAN_VALUES
