"""The active-learning next-batch ranker over stored predictions.

Driven at the store seam (``active_learn_tools.store``), the boundary
the parity harness uses. Covered here: the honest statuses (no
migration 029, no stored predictions), param validation with
remediation, the three strategy orders (margin/entropy/
random_incumbent seeded 42), the chunk-id tiebreak, the batch cap, and
6-place score rounding.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.tools import active_learn_tools

TENANT = "00000000-0000-0000-0000-000000000001"


class _AbsentPool:
    """to_regclass probes return None (migration 029 not applied)."""

    def __init__(self) -> None:
        self._conn = self

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "to_regclass" in sql:
            return None
        return None

    def acquire(self) -> _AbsentPool:
        return self

    async def __aenter__(self) -> _AbsentPool:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _PresentPool(_AbsentPool):
    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "to_regclass" in sql:
            return "public.chunk_predictions"
        return None


def _row(chunk_id: str, probability: float, set_id: str = "set-a") -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "set_id": set_id,
        "label": "alpha",
        "probability": probability,
        "calibrated_probability": None,
        "tier": "head",
        "review": False,
    }


def _patch_predictions(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]) -> list[int]:
    seen: list[int] = []

    async def fake_read(pool: Any, tenant_id: str, limit: int) -> list[dict[str, object]]:
        seen.append(limit)
        return rows

    monkeypatch.setattr(active_learn_tools.store, "read_predictions", fake_read)
    return seen


async def test_unknown_strategy_is_a_validation_error_with_remediation() -> None:
    with pytest.raises(ValidationError) as excinfo:
        await active_learn_tools.active_learn_next(
            _PresentPool(), tenant_id=TENANT, strategy="least_confident"
        )
    assert "margin" in excinfo.value.remediation


async def test_non_positive_batch_is_a_validation_error() -> None:
    with pytest.raises(ValidationError):
        await active_learn_tools.active_learn_next(_PresentPool(), tenant_id=TENANT, batch=0)


async def test_absent_tables_answer_unavailable() -> None:
    result = await active_learn_tools.active_learn_next(_AbsentPool(), tenant_id=TENANT)
    assert result["status"] == "unavailable"
    assert "migration 029" in str(result["reason"])
    assert result["ranked"] == []
    assert result["requested_batch"] == 10


async def test_no_predictions_answer_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_predictions(monkeypatch, [])
    result = await active_learn_tools.active_learn_next(_PresentPool(), tenant_id=TENANT)
    assert result["status"] == "empty"
    assert str(result["remedy"])


async def test_margin_ranks_the_least_confident_first(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row("c1", 0.99), _row("c2", 0.51), _row("c3", 0.80)]
    _patch_predictions(monkeypatch, rows)
    result = await active_learn_tools.active_learn_next(
        _PresentPool(), tenant_id=TENANT, strategy="margin", batch=10
    )
    assert result["status"] == "success"
    assert result["strategy"] == "margin"
    assert result["requested_batch"] == 10
    assert [row["chunk_id"] for row in result["ranked"]] == ["c2", "c3", "c1"]
    # The margin score is 1 - p: c2 ranks 0.49, c1 ranks 0.01.
    assert result["ranked"][0]["score"] == 0.49
    assert result["ranked"][2]["score"] == 0.01


async def test_margin_breaks_ties_by_chunk_id(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row("c-b", 0.5), _row("c-a", 0.5)]
    _patch_predictions(monkeypatch, rows)
    result = await active_learn_tools.active_learn_next(
        _PresentPool(), tenant_id=TENANT, strategy="margin"
    )
    assert [row["chunk_id"] for row in result["ranked"]] == ["c-a", "c-b"]


async def test_entropy_peaks_at_half_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row("c-sure", 0.99), _row("c-coin", 0.5), _row("c-fair", 0.9)]
    _patch_predictions(monkeypatch, rows)
    result = await active_learn_tools.active_learn_next(
        _PresentPool(), tenant_id=TENANT, strategy="entropy", batch=10
    )
    assert [row["chunk_id"] for row in result["ranked"]] == ["c-coin", "c-fair", "c-sure"]
    top = result["ranked"][0]
    assert top["score"] == pytest.approx(0.693147, abs=1e-6)


async def test_random_incumbent_is_seeded_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(f"c{i}", 0.5 + i * 0.01) for i in range(8)]
    _patch_predictions(monkeypatch, rows)
    first = await active_learn_tools.active_learn_next(
        _PresentPool(), tenant_id=TENANT, strategy="random_incumbent", batch=4
    )
    second = await active_learn_tools.active_learn_next(
        _PresentPool(), tenant_id=TENANT, strategy="random_incumbent", batch=4
    )
    assert [row["chunk_id"] for row in first["ranked"]] == [
        row["chunk_id"] for row in second["ranked"]
    ]
    assert len(first["ranked"]) == 4
    # Seeded shuffling must not equal the confidence order (it varies it).
    assert [row["chunk_id"] for row in first["ranked"]] != ["c0", "c1", "c2", "c3"]


async def test_batch_caps_ranked_but_keeps_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(f"c{i}", 0.5) for i in range(5)]
    _patch_predictions(monkeypatch, rows)
    result = await active_learn_tools.active_learn_next(
        _PresentPool(), tenant_id=TENANT, strategy="margin", batch=3
    )
    assert len(result["ranked"]) == 3
    assert result["requested_batch"] == 3


async def test_batch_above_the_pool_returns_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row("c1", 0.5)]
    _patch_predictions(monkeypatch, rows)
    result = await active_learn_tools.active_learn_next(
        _PresentPool(), tenant_id=TENANT, strategy="margin", batch=100
    )
    assert len(result["ranked"]) == 1
    assert result["requested_batch"] == 100


async def test_ranked_rows_carry_the_honest_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row("c1", 0.123456789)
    row["review"] = True
    _patch_predictions(monkeypatch, [row])
    result = await active_learn_tools.active_learn_next(
        _PresentPool(), tenant_id=TENANT, strategy="margin"
    )
    top = result["ranked"][0]
    assert top["label"] == "alpha"
    assert top["set_id"] == "set-a"
    assert top["probability"] == 0.123457  # rounded to 6 places
    assert top["review"] is True
