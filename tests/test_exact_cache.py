"""U42: provenance-keyed exact result cache tests (offline).

The cache contract is exercised against Protocol-typed fake connections
(SQL lives as module constants in exact_cache.py); the DB-backed tenant
isolation proof runs in the requires_postgres suites against migration 020.
"""

from __future__ import annotations

import json

import pytest

from corpus_kb.research.exact_cache import (
    CACHE_CONFIG_DEFAULTS,
    GET_SQL,
    PUT_SQL,
    CacheKeyComponents,
    canonical_decoding_params,
    compute_cache_key,
    get,
    load_cache_settings,
    normalize_prompt_static_first,
    put,
    sha256_text,
)


def _components(**overrides: str) -> CacheKeyComponents:
    base: dict[str, str] = {
        "unit_text_sha256": sha256_text("unit text"),
        "codebook_release_sha256": sha256_text("release-1"),
        "prompt_template_sha256": sha256_text("template v1"),
        "model_digest": "sha256:abc123",
        "decoding_params_json": canonical_decoding_params({"temperature": 0, "seed": 0}),
        "schema_sha256": sha256_text("schema v1"),
    }
    base.update(overrides)
    return CacheKeyComponents(**base)


def test_cache_key_changes_when_any_component_changes() -> None:
    baseline = compute_cache_key(_components())
    # Unit text
    assert compute_cache_key(_components(unit_text_sha256="b" * 64)) != baseline
    # Codebook release
    assert compute_cache_key(_components(codebook_release_sha256="c" * 64)) != baseline
    # Prompt template
    assert compute_cache_key(_components(prompt_template_sha256="d" * 64)) != baseline
    # Model digest
    assert compute_cache_key(_components(model_digest="sha256:other")) != baseline
    # Decoding params
    assert (
        compute_cache_key(
            _components(
                decoding_params_json=canonical_decoding_params({"temperature": 0, "seed": 1})
            )
        )
        != baseline
    )
    # Schema
    assert compute_cache_key(_components(schema_sha256="e" * 64)) != baseline
    # Stability: identical components derive an identical key.
    assert compute_cache_key(_components()) == baseline
    assert len(baseline) == 64


def test_canonical_decoding_params_is_key_order_insensitive() -> None:
    assert canonical_decoding_params({"a": 1, "b": 2}) == canonical_decoding_params(
        {"b": 2, "a": 1}
    )
    assert canonical_decoding_params({"temperature": 0}) == '{"temperature":0}'


def test_normalize_prompt_static_first_puts_unit_last() -> None:
    prompt = normalize_prompt_static_first("Instructions:\nCodebook text here.", "UNIT TEXT")
    assert prompt.index("Instructions:") < prompt.index("Codebook text here.")
    assert prompt.endswith("UNIT TEXT")
    # Static prefix is shared verbatim across units (prefix-reuse contract).
    shared = normalize_prompt_static_first("Instructions:\nCodebook text here.", "OTHER UNIT")
    assert prompt[: prompt.index("UNIT")] == shared[: shared.index("OTHER")]


class _FakeCacheConn:
    """Protocol-typed asyncpg stand-in: records statements, table-backed."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, object]] = {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, sql: str, *args: object) -> object | None:
        key = (str(args[0]), str(args[1]))  # (cache_key, tenant_id)
        if "WHERE cache_key = $1 AND tenant_id = $2" in sql:
            return self.rows.get(key)
        raise AssertionError(f"unexpected GET statement: {sql!r}")

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        if "ON CONFLICT (cache_key) DO NOTHING" in sql:
            key = (str(args[0]), str(args[1]))
            self.rows.setdefault(
                key,
                {
                    "response": json.loads(str(args[3])),
                    "model_name": str(args[4]),
                    "created_at": "fixture",
                },
            )
            return "INSERT 0 1"
        raise AssertionError(f"unexpected PUT statement: {sql!r}")


async def test_put_then_get_round_trip_hits() -> None:
    conn = _FakeCacheConn()
    components = _components()
    key = compute_cache_key(components)
    await put(conn, "tenant-a", key, components, {"code": "billing"}, "qwen3:4b", "sha256:abc")
    hit = await get(conn, "tenant-a", key)
    assert hit is not None
    assert hit["code"] == "billing"


async def test_cache_miss_returns_none() -> None:
    conn = _FakeCacheConn()
    assert await get(conn, "tenant-a", "f" * 64) is None


async def test_no_cross_tenant_read() -> None:
    conn = _FakeCacheConn()
    components = _components()
    key = compute_cache_key(components)
    await put(conn, "tenant-a", key, components, {"code": "billing"}, "qwen3:4b", "d")
    # Same key, different tenant: the WHERE-clause scope makes it a miss.
    assert await get(conn, "tenant-b", key) is None
    # And the GET statement itself carries the tenant predicate.
    assert "AND tenant_id = $2" in GET_SQL


async def test_put_is_idempotent_conflict_keeps_original() -> None:
    conn = _FakeCacheConn()
    components = _components()
    key = compute_cache_key(components)
    await put(conn, "tenant-a", key, components, {"code": "billing"}, "qwen3:4b", "d")
    await put(conn, "tenant-a", key, components, {"code": "OTHER"}, "qwen3:4b", "d")
    hit = await get(conn, "tenant-a", key)
    assert hit is not None and hit["code"] == "billing"
    assert "ON CONFLICT (cache_key) DO NOTHING" in PUT_SQL


def test_cache_settings_mode_posture() -> None:
    assert CACHE_CONFIG_DEFAULTS == {
        "enabled": True,
        "deductive_enabled": True,
        "inductive_enabled": False,
    }
    defaults = load_cache_settings({})
    assert defaults.allows("deductive") is True
    assert defaults.allows("inductive") is False
    # Master off disables both modes.
    off = load_cache_settings({"cache": {"enabled": False}})
    assert off.allows("deductive") is False
    # Seeded inductive discovery opts in explicitly.
    seeded = load_cache_settings({"cache": {"inductive_enabled": True}})
    assert seeded.allows("inductive") is True
    with pytest.raises(ValueError, match="mode"):
        defaults.allows(" exploratory ")


def test_cache_settings_rejects_non_bool() -> None:
    with pytest.raises(ValueError, match="enabled"):
        load_cache_settings({"cache": {"enabled": "yes"}})
