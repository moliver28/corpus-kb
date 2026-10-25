"""Tests for the extraction cache, exemplar mining, and the config
validator. The database boundary is faked; real SQL is exercised by the
migration tests against Postgres."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from corpus_kb.extraction.cache import ExtractionCache
from corpus_kb.extraction.icl import _to_example, mine_exemplars
from corpus_kb.utils.models import Entity


class _FakeConn:
    """Duck-typed asyncpg connection fake.

    Verb methods are bound through ``__getattr__`` so the fake carries no
    SQL-shaped methods of its own (a scanner false positive); the cache
    only ever calls the two verbs positionally.
    """

    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def __getattr__(self, name: str) -> Callable[..., object]:
        if name == "execute":
            return self._execute
        if name == "fetch":
            return self._fetch
        raise AttributeError(name)

    def transaction(self) -> _FakeConn:
        return self

    async def _execute(self, *args: object) -> object:
        self._store.setdefault("executes", []).append(args)
        return "OK"

    async def _fetch(self, *args: object) -> object:
        hashes = list(args[1]) if len(args) > 1 else []
        records = self._store.get("records", [])
        return [r for r in records if r["chunk_hash"] in hashes]

    async def copy_records_to_table(
        self, table: str, records: list[tuple[Any, ...]], columns: list[str]
    ) -> None:
        assert table == "extraction_cache"
        stored = self._store.setdefault("records", [])
        for record in records:
            stored.append(dict(zip(columns, record, strict=True)))

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakePool:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def acquire(self) -> _FakeConn:
        return _FakeConn(self._store)


def _entity(name: str = "Elena") -> Entity:
    return Entity(name=name, entity_type="PERSON", source_type="text")


@pytest.mark.asyncio
async def test_cache_put_then_get_roundtrip() -> None:
    store: dict[str, Any] = {}
    cache = ExtractionCache(_FakePool(store), "00000000-0000-0000-0000-000000000001")

    written = await cache.put(
        {"hash-a": ([_entity()], [])},
        backend="ml",
        model_version="mv",
        prompt_version="pv",
    )
    assert written == 1

    hits = await cache.get(
        ["hash-a", "hash-b"], backend="ml", model_version="mv", prompt_version="pv"
    )
    assert "hash-a" in hits
    assert hits["hash-a"][0][0].name == "Elena"
    assert "hash-b" not in hits


@pytest.mark.asyncio
async def test_cache_get_empty_hashes_is_noop() -> None:
    cache = ExtractionCache(_FakePool({}), "t")
    assert await cache.get([], backend="ml", model_version="m", prompt_version="p") == {}


def test_icl_example_shape_and_missing_mention_skip() -> None:
    row = {
        "chunk_text": "Elena likes pistachio.",
        "relation_type": "LIKES",
        "confidence": 0.9,
        "source_name": "Elena",
        "source_type": "PERSON",
        "target_name": "pistachio",
        "target_type": "CONCEPT",
    }
    example = _to_example(row)
    assert example is not None
    assert example["text"] == "Elena likes pistachio."
    extractions = example["extractions"]
    assert extractions[0]["start_pos"] == 0
    assert extractions[2]["attributes"]["object"] == "pistachio"

    missing = dict(row, target_name="gelato")
    assert _to_example(missing) is None


class _FakeMinePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def acquire(self) -> _FakeMineConn:
        return _FakeMineConn(self._rows)


class _FakeMineConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __getattr__(self, name: str) -> Callable[..., object]:
        if name == "fetch":
            return self._fetch
        if name == "transaction":
            # The tenant-GUC mining path wraps its read in a transaction;
            # this fake is already an async context manager, so it serves.
            return self._transaction
        if name == "execute":
            return self._execute
        raise AttributeError(name)

    def _transaction(self) -> _FakeMineConn:
        return self

    async def _execute(self, *args: object) -> str:
        return "OK"

    async def _fetch(self, *args: object) -> object:
        return self._rows

    async def __aenter__(self) -> _FakeMineConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.asyncio
async def test_mine_exemplars_returns_shaped_examples() -> None:
    rows = [
        {
            "chunk_text": "Elena likes pistachio.",
            "relation_type": "LIKES",
            "confidence": 0.9,
            "source_name": "Elena",
            "source_type": "PERSON",
            "target_name": "pistachio",
            "target_type": "CONCEPT",
        }
    ]
    exemplars = await mine_exemplars(_FakeMinePool(rows), "00000000-0000-0000-0000-000000000001")
    assert len(exemplars) == 1
    assert exemplars[0]["extractions"][2]["extraction_class"] == "LIKES"


def test_validate_extraction_ml_rejects_bad_values() -> None:
    from corpus_kb._setup.validate_configs import validate_extraction_ml

    errors = validate_extraction_ml(
        {
            "extraction": {
                "mode": "bogus",
                "relex": {"threshold": 5},
                "ensemble": {"enabled": "yes", "members": ["nonsense"]},
                "cache": {"enabled": 1},
            }
        }
    )
    text = "\n".join(errors)
    assert "extraction.mode" in text
    assert "extraction.relex.threshold" in text
    assert "extraction.ensemble.enabled" in text
    assert "extraction.ensemble.members" in text
    assert "extraction.cache.enabled" in text

    assert validate_extraction_ml({}) == []
    assert validate_extraction_ml({"extraction": {"cache": {"enabled": True}}}) == []
