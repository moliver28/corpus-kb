"""U35: the judgment-distribution mean wired at its production call site.

Exercises the REAL dispatch_chunk offline: the model side is a fake
probability source (deterministic confidence sequence per draw — the same
shape the Ollama logprobs backend exposes, see rag/reranker.py), the DB side
is a minimal asyncpg-shaped fake (tenant_connection + the four SQL shapes
dispatch_chunk issues). Pins:
  * mean ON  — JUDGMENT_MEAN_SAMPLES extra draws, route on the mean,
    judgment summary on the row, greedy stays the decision source;
  * mean OFF — byte-identical rows, exactly one model call;
  * the live (call_fn=None) temperature contract: greedy 0, draws 0.7.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

import pytest

from corpus_kb.coding import coder_client, judgment_sampling
from corpus_kb.coding.coder_dispatch import dispatch_chunk
from corpus_kb.config import get_default_config

TENANT = UUID("00000000-0000-0000-0000-000000000351")
CHUNK = uuid4()
CODE = "code_presence"
CHUNK_TEXT = "the participant said: we moved the store closures last spring"


class _FakeTransaction:
    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakeConnection:
    """Just enough asyncpg for dispatch_chunk's SQL shapes."""

    def __init__(self) -> None:
        self.inserted_chunk_codes: list[tuple[object, ...]] = []
        self.inserted_final_codes = 0

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetchval(self, query: str, *args: object) -> object:
        if query.startswith("SELECT text FROM chunks"):
            return CHUNK_TEXT
        if "FROM chunk_signals" in query:
            return None
        if "INSERT INTO chunk_codes" in query:
            self.inserted_chunk_codes.append(args)
            return uuid4()
        raise AssertionError(f"unexpected fetchval: {query[:80]!r}")

    async def fetchrow(self, _query: str, *_args: object) -> dict[str, object] | None:
        return None  # no chunk_status row -> qa chaining inert

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        if "FROM code_registry" in query:
            code_ids = cast_code_ids(args[1])
            return [
                {
                    "code_id": cid,
                    "brief_definition": "d",
                    "inclusion_criteria": "i",
                    "exclusion_criteria": "e",
                    "is_provisional": False,
                }
                for cid in code_ids
            ]
        raise AssertionError(f"unexpected fetch: {query[:80]!r}")

    async def execute(self, query: str, *_args: object) -> str:
        if "set_config" in query:  # tenant_connection's RLS context set
            return "OK"
        if "INSERT INTO final_codes" in query:
            self.inserted_final_codes += 1
            return "OK"
        raise AssertionError(f"unexpected execute: {query[:80]!r}")


def cast_code_ids(raw: object) -> list[str]:
    """The $2::text[] argument dispatch passes (a list of str)."""
    assert isinstance(raw, list)
    return [str(cid) for cid in raw]


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConnection()

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


def _reply(confidence: float) -> dict[str, Any]:
    return {
        "decisions": [
            {
                "code_id": CODE,
                "decision": "reject",
                "confidence": confidence,
                "rationale": "criterion not met in this segment",
            }
        ],
        "logprobs": [],
    }


class _SequenceSource:
    """Fake probability source: one recorded window per draw, fixed confidences."""

    def __init__(self, confidences: list[float]) -> None:
        self.confidences = confidences
        self.windows: list[str] = []

    async def __call__(self, window: str) -> dict[str, Any]:
        self.windows.append(window)
        index = min(len(self.windows) - 1, len(self.confidences) - 1)
        return _reply(self.confidences[index])


async def test_mean_on_routes_on_distribution_mean() -> None:
    pool = _FakePool()
    source = _SequenceSource([0.9, 0.5, 0.6, 0.7, 0.8])  # greedy first, then draws
    result = await dispatch_chunk(
        pool,
        TENANT,
        CHUNK,
        [CODE],
        call_fn=source,
        use_judgment_mean=True,
    )
    assert result["status"] == "success"
    assert len(source.windows) == 1 + judgment_sampling.JUDGMENT_MEAN_SAMPLES
    row = result["results"][0]
    assert row["judgment"]["used_mean"] is True
    assert row["judgment"]["n_samples"] == 5
    assert row["judgment"]["status"] == "ok"
    assert row["confidence"] == pytest.approx(0.7)  # mean of the 5 draws
    # Routing actually moved with the mean: greedy 0.9 -> high-conf-reject,
    # mean 0.7 -> mid-band (Bands.high=0.90, mid=0.70).
    assert row["route"] == "mid-band"
    assert row["routed_confidence"] == pytest.approx(0.7)
    # The greedy generation stays the decision source; only confidence is
    # the distribution mean.
    assert row["decision"] == "reject"
    # The persisted chunk_codes confidence is the routed-effective (mean) one.
    inserted = pool.conn.inserted_chunk_codes[0]
    assert inserted[8] == pytest.approx(0.7)


async def test_mean_off_is_byte_identical_single_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(explicit: bool | None) -> list[dict[str, Any]]:
        pool = _FakePool()
        source = _SequenceSource([0.9])
        result = await dispatch_chunk(
            pool, TENANT, CHUNK, [CODE], call_fn=source, use_judgment_mean=explicit
        )
        assert len(source.windows) == 1
        return result["results"]

    monkeypatch.setattr(judgment_sampling, "load_config", get_default_config)
    off_rows = await run(False)
    default_rows = await run(None)  # resolves the config key: use_mean is False
    assert off_rows == default_rows
    assert "judgment" not in off_rows[0]


def _default_config_mutated(key: str, value: object) -> dict[str, object]:
    """A deep-copied default config with one judgment_distribution key set.

    Deep copy is load-bearing: _apply_research_defaults shares the module
    MEASUREMENT_CONFIG_DEFAULTS dicts by reference, so mutating a bare
    get_default_config() result would leak across tests (and callers).
    """
    config = deepcopy(get_default_config())
    research = config["research"]
    assert isinstance(research, dict)
    jd = research["judgment_distribution"]
    assert isinstance(jd, dict)
    jd[key] = value
    return config


async def test_config_key_enables_mean(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _default_config_mutated("use_mean", True)
    monkeypatch.setattr(judgment_sampling, "load_config", lambda: config)

    pool = _FakePool()
    source = _SequenceSource([0.9, 0.5, 0.6, 0.7, 0.8])
    result = await dispatch_chunk(pool, TENANT, CHUNK, [CODE], call_fn=source)
    assert len(source.windows) == 5  # the key turned sampling on
    row = result["results"][0]
    assert row["confidence"] == pytest.approx(0.7)
    assert row["judgment"]["used_mean"] is True


async def test_live_path_temperature_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """call_fn=None: greedy generation at temperature 0, draws at 0.7."""
    temperatures: list[float] = []

    def fake_generate(
        window: str, model: str, base_url: str, temperature: float = 0
    ) -> tuple[Any, Any]:
        temperatures.append(temperature)
        return json.dumps(_reply(0.9)), []

    monkeypatch.setattr(coder_client, "_generate", fake_generate)
    pool = _FakePool()
    result = await dispatch_chunk(
        pool, TENANT, CHUNK, [CODE], model="fake-model", use_judgment_mean=True
    )
    assert temperatures == [
        0,
        judgment_sampling.JUDGMENT_SAMPLE_TEMPERATURE,
        judgment_sampling.JUDGMENT_SAMPLE_TEMPERATURE,
        judgment_sampling.JUDGMENT_SAMPLE_TEMPERATURE,
        judgment_sampling.JUDGMENT_SAMPLE_TEMPERATURE,
    ]
    assert result["results"][0]["judgment"]["n_samples"] == 5


async def test_unparseable_draw_skipped_honestly() -> None:
    class _FlakySource:
        """Fake probability source whose second draw degrades (unparseable)."""

        def __init__(self) -> None:
            self.windows: list[str] = []
            self._good_calls = 0

        async def __call__(self, window: str) -> dict[str, Any]:
            self.windows.append(window)
            if len(self.windows) == 2:
                return {"error": "ConnectionError: ollama down"}
            self._good_calls += 1
            values = [0.9, 0.5, 0.6, 0.7, 0.8]
            return _reply(values[min(self._good_calls - 1, len(values) - 1)])

    pool = _FakePool()
    source = _FlakySource()
    result = await dispatch_chunk(
        pool, TENANT, CHUNK, [CODE], call_fn=source, use_judgment_mean=True
    )
    assert len(source.windows) == 5  # greedy + JUDGMENT_MEAN_SAMPLES draws
    row = result["results"][0]
    # The bad draw was skipped, not counted: greedy + 3 valid draws.
    assert row["judgment"]["n_samples"] == 4
    assert row["confidence"] == pytest.approx((0.9 + 0.5 + 0.6 + 0.7) / 4)
    assert row["judgment"]["used_mean"] is True


class TestJudgmentMeanEnabled:
    """The typed config read behind the call site."""

    def test_default_off(self):
        assert judgment_sampling.judgment_mean_enabled(get_default_config()) is False

    def test_key_on(self):
        config = _default_config_mutated("use_mean", True)
        assert judgment_sampling.judgment_mean_enabled(config) is True

    def test_non_mapping_research_raises(self):
        config = {"research": "nope"}
        with pytest.raises(ValueError, match=r"config.research must be a mapping"):
            judgment_sampling.judgment_mean_enabled(config)

    def test_missing_blocks_resolve_to_typed_defaults(self):
        # measurement_settings fills defaults for absent blocks: an honest OFF.
        assert judgment_sampling.judgment_mean_enabled({"research": {}}) is False

    def test_non_mapping_block_raises_loud(self):
        config = {"research": {"judgment_distribution": "yes"}}
        with pytest.raises(ValueError, match=r"judgment_distribution must be a mapping"):
            judgment_sampling.judgment_mean_enabled(config)
