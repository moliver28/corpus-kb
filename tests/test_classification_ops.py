"""Classification ops: honest guards, the build write, and the tier seam.

The pool is faked at the asyncpg boundary (the ``tests/mock_pg`` pattern):
a recording connection answers ``to_regclass`` probes, serves the stored
vector rows, and captures the wholesale write. Tier B runs against a
fake SetFit module substituted at ``tier_b.load_setfit`` — no network, no
download, and the frozen-body contract is asserted on recorded kwargs.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from corpus_kb.classification import engine, tier_b
from corpus_kb.errors import DependencyMissingError
from corpus_kb.tools import classification_tools
from tests.mock_pg import transaction_cm

TENANT = "00000000-0000-0000-0000-000000000001"
SET_ID = "00000000-0000-0000-0000-000000000009"
ALPHA_CENTER = [1.0, 0.0]
BETA_CENTER = [0.0, 1.0]

OFF = {}
HEAD_ON = {
    "analysis": {"classification": {"enabled": True, "tier": "head", "min_labels_per_class": 8}}
}
SETFIT_ON = {
    "analysis": {"classification": {"enabled": True, "tier": "setfit", "min_labels_per_class": 8}}
}


def _uuid(i: int) -> str:
    return f"00000000-0000-0000-0000-{i:012d}"


def _rows(alpha: int = 8, beta: int = 8, extra: int = 4) -> list[dict[str, object]]:
    """Embedded-chunk rows: two separable classes plus unseeded chunks."""
    rows: list[dict[str, object]] = []
    index = 0
    for count, center, text in (
        (alpha, ALPHA_CENTER, "alpha document"),
        (beta, BETA_CENTER, "beta document"),
    ):
        for _ in range(count):
            vector = [center[0] + 0.02 * (index % 3), center[1] + 0.01 * (index % 2)]
            rows.append(
                {
                    "chunk_id": _uuid(index),
                    "text": f"{text} {index}",
                    "embedding": json.dumps(vector),
                }
            )
            index += 1
    for _ in range(extra):
        rows.append(
            {
                "chunk_id": _uuid(index),
                "text": f"extra alpha-like {index}",
                "embedding": json.dumps([0.95, 0.05]),
            }
        )
        index += 1
    return rows


def _seed_file(tmp_path: Path, rows: list[dict[str, object]], per_class: int = 8) -> Path:
    labels = ["alpha"] * per_class + ["beta"] * per_class
    payload = [
        {"chunk_id": str(row["chunk_id"]), "label": label}
        for row, label in zip(rows[: 2 * per_class], labels, strict=True)
    ]
    path = tmp_path / "seeds.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakeConn:
    """A recording asyncpg connection (probe, read, and write paths)."""

    def __init__(self, vector_rows: list[dict[str, object]], tables: bool = True) -> None:
        self.vector_rows = vector_rows
        self.prediction_rows: list[dict[str, object]] = []
        self.tables = tables
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.inserted_predictions: list[tuple[object, ...]] = []
        self.label_set_args: tuple[object, ...] | None = None

    def transaction(self) -> object:
        return transaction_cm()

    async def execute(self, sql: str, *args: object) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((sql, args))
        if "chunks_vectors" in sql:
            _tenant, last, limit = args
            rows = [
                row for row in self.vector_rows if last is None or str(row["chunk_id"]) > str(last)
            ]
            return rows[: int(limit)] if isinstance(limit, int) else rows
        if "FROM chunk_predictions" in sql:
            return self.prediction_rows
        return []

    async def fetchval(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        if "to_regclass" in sql:
            return "public.table" if self.tables else None
        if "INSERT INTO label_sets" in sql:
            self.label_set_args = args
            return SET_ID
        return None

    async def executemany(self, sql: str, rows: list[tuple[object, ...]]) -> None:
        self.executed.append((sql, tuple(rows)))
        self.inserted_predictions.extend(rows)


class FakePool:
    """Yields one recording connection (both the probe and the GUC path)."""

    def __init__(self, vector_rows: list[dict[str, object]], tables: bool = True) -> None:
        self.conn = FakeConn(vector_rows, tables=tables)

    def acquire(self) -> object:
        conn = self.conn

        @asynccontextmanager
        async def _one() -> object:
            yield conn

        return _one()


# ---------------------------------------------------------------------------
# Honest guards
# ---------------------------------------------------------------------------


async def test_disabled_gate_is_an_honest_noop_and_never_touches_the_pool() -> None:
    pool = FakePool([])
    result = await classification_tools.classification_build(pool, config=OFF, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert "analysis.classification.enabled" in str(result["reason"])
    assert pool.conn.executed == []


async def test_pre_migration_tables_answer_unavailable() -> None:
    pool = FakePool([], tables=False)
    built = await classification_tools.classification_build(
        pool, config=HEAD_ON, tenant_id=TENANT, seed_file="seeds.json"
    )
    listed = await classification_tools.classification_list(pool, config=HEAD_ON, tenant_id=TENANT)
    assert built["status"] == "unavailable"
    assert "migration 029" in str(built["reason"])
    assert listed["status"] == "unavailable"


async def test_missing_seed_file_is_insufficient_and_names_the_schema() -> None:
    pool = FakePool([])
    result = await classification_tools.classification_build(
        pool, config=HEAD_ON, tenant_id=TENANT, seed_file=None
    )
    assert result["status"] == "insufficient_labels"
    assert "No seed file was given" in str(result["reason"])
    assert "'chunk_id'" in str(result["remedy"])


async def test_unreadable_seed_file_names_the_path(tmp_path: Path) -> None:
    pool = FakePool([])
    result = await classification_tools.classification_build(
        pool, config=HEAD_ON, tenant_id=TENANT, seed_file=str(tmp_path / "absent.json")
    )
    assert result["status"] == "insufficient_labels"
    assert "absent.json" in str(result["reason"])


async def test_single_class_seed_set_is_insufficient(tmp_path: Path) -> None:
    rows = _rows()
    path = tmp_path / "one.json"
    path.write_text(
        json.dumps([{"chunk_id": str(row["chunk_id"]), "label": "alpha"} for row in rows[:9]]),
        encoding="utf-8",
    )
    pool = FakePool(rows)
    result = await classification_tools.classification_build(
        pool, config=HEAD_ON, tenant_id=TENANT, seed_file=str(path)
    )
    assert result["status"] == "insufficient_labels"
    assert "at least 2" in str(result["reason"])


async def test_class_below_floor_lists_per_class_counts(tmp_path: Path) -> None:
    rows = _rows(alpha=8, beta=8)
    path = tmp_path / "thin.json"
    payload = [{"chunk_id": str(row["chunk_id"]), "label": "alpha"} for row in rows[:8]]
    payload += [{"chunk_id": str(row["chunk_id"]), "label": "beta"} for row in rows[8:11]]
    path.write_text(json.dumps(payload), encoding="utf-8")
    pool = FakePool(rows)
    result = await classification_tools.classification_build(
        pool, config=HEAD_ON, tenant_id=TENANT, seed_file=str(path)
    )
    assert result["status"] == "insufficient_labels"
    reason = str(result["reason"])
    assert "at least 8 seeds" in reason
    assert "alpha=8" in reason
    assert "beta=3" in reason


async def test_unresolvable_seed_ids_are_insufficient(tmp_path: Path) -> None:
    path = tmp_path / "orphan.json"
    path.write_text(
        json.dumps(
            [
                {"chunk_id": _uuid(500), "label": "alpha"},
                {"chunk_id": _uuid(501), "label": "beta"},
            ]
        ),
        encoding="utf-8",
    )
    pool = FakePool(_rows())
    result = await classification_tools.classification_build(
        pool, config=HEAD_ON, tenant_id=TENANT, seed_file=str(path)
    )
    assert result["status"] == "insufficient_labels"
    assert "no stored embedding" in str(result["reason"])


# ---------------------------------------------------------------------------
# The head-tier build (wholesale write), the warning, and the bounded list
# ---------------------------------------------------------------------------


async def test_head_build_writes_calibrated_predictions(tmp_path: Path) -> None:
    rows = _rows()
    seed_path = _seed_file(tmp_path, rows)
    pool = FakePool(rows)
    result = await classification_tools.classification_build(
        pool, config=HEAD_ON, tenant_id=TENANT, seed_file=str(seed_path)
    )
    assert result["status"] == "success"
    assert result["prediction_count"] == len(rows)
    assert result["class_count"] == 2
    reviews = sum(1 for row in pool.conn.inserted_predictions if row[7] is True)
    assert result["review_count"] == reviews
    params = result["params"]
    digest = hashlib.sha256(seed_path.read_bytes()).hexdigest()[:12]
    assert params["seed_file"] == "seeds.json"
    assert params["seed_file_sha256"] == digest
    assert params["vectors_scored"] == len(rows)
    assert params["tier"] == "head"
    assert params["calibration"] == "platt"
    assert params["review_threshold"] == classification_tools.REVIEW_THRESHOLD
    assert params["sklearn_version"]
    assert "warnings" not in params
    # The write: one label_sets row, then wholesale predictions per set.
    assert pool.conn.label_set_args is not None
    assert json.loads(str(pool.conn.label_set_args[2])) == ["alpha", "beta"]
    assert len(pool.conn.inserted_predictions) == len(rows)
    first = pool.conn.inserted_predictions[0]
    assert first[0] == TENANT and first[2] == SET_ID and first[6] == "head"
    assert 0.0 <= float(first[4]) <= 1.0
    assert json.loads(str(first[8]))["tier"] == "head"
    # Separable corpus: the unseeded alpha-like extras classify as alpha.
    extra_labels = {row[3] for row in pool.conn.inserted_predictions[-4:]}
    assert extra_labels == {"alpha"}


async def test_many_class_build_records_the_degradation_warning(tmp_path: Path) -> None:
    count = 25
    rows: list[dict[str, object]] = []
    seeds_payload: list[dict[str, str]] = []
    for i in range(count):
        center = float(i)
        rows.append(
            {
                "chunk_id": _uuid(i),
                "text": f"class {i} text",
                "embedding": json.dumps([center, 1.2]),
            }
        )
        rows.append(
            {
                "chunk_id": _uuid(100 + i),
                "text": f"class {i} second",
                "embedding": json.dumps([center, 0.8]),
            }
        )
        seeds_payload.append({"chunk_id": _uuid(i), "label": f"class{i}"})
        seeds_payload.append({"chunk_id": _uuid(100 + i), "label": f"class{i}"})
    path = tmp_path / "many.json"
    path.write_text(json.dumps(seeds_payload), encoding="utf-8")
    pool = FakePool(rows)
    many_on = {
        "analysis": {"classification": {"enabled": True, "tier": "head", "min_labels_per_class": 2}}
    }
    result = await classification_tools.classification_build(
        pool, config=many_on, tenant_id=TENANT, seed_file=str(path)
    )
    assert result["status"] == "success"
    assert result["class_count"] == count
    warnings = result["params"]["warnings"]
    assert any("many classes" in str(item) for item in warnings)


async def test_list_reads_a_bounded_deterministic_window() -> None:
    stored = [
        {
            "chunk_id": _uuid(1),
            "set_id": SET_ID,
            "label": "alpha",
            "probability": 0.9,
            "calibrated_probability": 0.95,
            "tier": "head",
            "review": False,
        }
    ]
    pool = FakePool([])
    pool.conn.prediction_rows = stored
    result = await classification_tools.classification_list(
        pool, config=OFF, tenant_id=TENANT, limit=9999
    )
    assert result["status"] == "success"
    assert result["prediction_count"] == 1
    row = result["predictions"][0]
    assert row["chunk_id"] == _uuid(1) and row["set_id"] == SET_ID
    assert row["tier"] == "head" and row["review"] is False
    read_sql, read_args = next(
        (sql, args) for sql, args in pool.conn.executed if "FROM chunk_predictions" in sql
    )
    assert "ORDER BY set_id, chunk_id" in read_sql
    assert read_args[1] == classification_tools.MAX_LIST_LIMIT


async def test_vector_reader_paginates_in_deterministic_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "READ_BATCH", 2)
    rows = _rows(alpha=3, beta=3, extra=0)
    pool = FakePool(rows)
    vectors = await engine.read_stored_vectors(pool, TENANT)
    assert [vector.chunk_id for vector in vectors] == [str(row["chunk_id"]) for row in rows]
    assert vectors[0].vector.dtype == np.float32
    fetches = [args for sql, args in pool.conn.executed if "chunks_vectors" in sql]
    assert len(fetches) == 4  # three full batches, then the empty short batch ends the loop
    assert fetches[1][1] == str(rows[1]["chunk_id"])  # keyset continuation


# ---------------------------------------------------------------------------
# The tier-B seam (fake setfit; the frozen-body contract on recorded kwargs)
# ---------------------------------------------------------------------------


class FakeBody:
    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    def encode(self, sentences: list[str]) -> list[list[float]]:
        return [self._table[text] for text in sentences]


class FakeSetFitModel:
    """The SetFitModel surface, recording the contract this tier must hold."""

    table: ClassVar[dict[str, list[float]]] = {}
    from_pretrained_calls: ClassVar[list[dict[str, object]]] = []
    fit_calls: ClassVar[list[dict[str, object]]] = []

    def __init__(self) -> None:
        self.model_body = FakeBody(type(self).table)
        self.model_head = LogisticRegression(solver="lbfgs")

    @classmethod
    def from_pretrained(cls, model_id: str, *, device: str) -> FakeSetFitModel:
        cls.from_pretrained_calls.append({"model_id": model_id, "device": device})
        return cls()

    def fit(self, x_train: list[str], y_train: list[str], *, end_to_end: bool = False) -> object:
        type(self).fit_calls.append({"end_to_end": end_to_end, "n": len(x_train)})
        self.model_head.fit(self.model_body.encode(list(x_train)), list(y_train))
        return self


class FakeTorch:
    seed: ClassVar[int | None] = None
    deterministic: ClassVar[bool | None] = None

    def manual_seed(self, value: int) -> None:
        type(self).seed = value

    def use_deterministic_algorithms(self, flag: bool) -> None:
        type(self).deterministic = flag


def _install_fake_setfit(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]) -> None:
    """Substitute the lazy imports; no test ever downloads a model."""
    FakeSetFitModel.table = {
        str(row["text"]): [1.0, 0.0] if "alpha" in str(row["text"]) else [0.0, 1.0] for row in rows
    }
    FakeSetFitModel.from_pretrained_calls = []
    FakeSetFitModel.fit_calls = []
    FakeTorch.seed = None
    FakeTorch.deterministic = None
    module = SimpleNamespace(SetFitModel=FakeSetFitModel, __version__="9.9.9-fake")
    monkeypatch.setattr(tier_b, "load_setfit", lambda: module)
    monkeypatch.setattr(tier_b, "_load_torch", lambda: FakeTorch())


async def test_setfit_tier_loads_cpu_frozen_body_and_pins_the_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _rows()
    seed_path = _seed_file(tmp_path, rows)
    _install_fake_setfit(monkeypatch, rows)
    pool = FakePool(rows)
    result = await classification_tools.classification_build(
        pool, config=SETFIT_ON, tenant_id=TENANT, seed_file=str(seed_path)
    )
    assert result["status"] == "success"
    load = FakeSetFitModel.from_pretrained_calls[0]
    assert load == {"model_id": "BAAI/bge-small-en-v1.5", "device": "cpu"}
    assert FakeSetFitModel.fit_calls[0]["end_to_end"] is False
    assert FakeTorch.seed == 42 and FakeTorch.deterministic is True
    params = result["params"]
    assert params["tier"] == "setfit"
    assert params["end_to_end"] is False
    assert params["device"] == "cpu"
    assert params["torch_seed"] == 42
    assert params["deterministic_mode"] == "enforced"


def test_missing_setfit_extra_raises_the_typed_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(name: str) -> object:
        raise ModuleNotFoundError(f"No module named {name!r}")

    monkeypatch.setattr(tier_b, "_import_module", _raise)
    with pytest.raises(DependencyMissingError) as excinfo:
        tier_b.fit_setfit(["text one", "text two"], ["alpha", "beta"])
    assert "corpus-kb[setfit]" in excinfo.value.remediation
