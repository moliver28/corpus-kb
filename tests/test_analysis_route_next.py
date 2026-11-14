"""The ``next_methods`` envelope key: derivation, order, caps, honesty.

The register's ``next:`` chain is the only source: the envelope copies
register names verbatim, orders targets by ascending difficulty then id,
caps at three, and hands the novice a ``corpus-kb`` command per target.
The CLI hints derive from ``surface_registry`` (the same source the
doctor journey pins), so the guard test fails when a register op gains
no hint. Degraded and ambiguous answers carry ``[]`` — the key is always
present, and an empty list never pretends a next step exists.
"""

from __future__ import annotations

import json
from typing import cast

import asyncpg
import pytest

from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.tools.analysis_route_next import (
    _NEXT_CAP,
    cli_run_hint,
    next_methods_for,
)
from corpus_kb.tools.analysis_route_tools import analysis_route
from tests.test_analysis_route_tools import (
    _PGML,
    _TIE_A,
    _TIE_B,
    QUESTION,
    _FlatEmbedder,
    _record,
    _register,
    _SplitEmbedder,
)

# -- The derivation on plain rows ------------------------------------------


def test_orders_by_difficulty_then_id() -> None:
    """Authored order never wins: easiest first, ties break by id."""
    register = _register(
        _record("01", "C method", op="duplicates_screen", difficulty="advanced"),
        _record("02", "A method", op="duplicates_screen", difficulty="novice"),
        _record("03", "B method", op="duplicates_screen", difficulty="intermediate"),
        _record(
            "05",
            "Cluster method",
            op="clusters_inspect",
            difficulty="intermediate",
            next_=["03", "02", "01"],
        ),
    )
    items = next_methods_for(register, register.methods[3])
    assert [item["method_id"] for item in items] == ["02", "03", "01"]


def test_caps_at_three_after_ordering() -> None:
    """Four targets, three items: the cap drops the hardest last row."""
    register = _register(
        *[
            _record(id_, f"M {id_}", op="duplicates_screen", difficulty="novice")
            for id_ in ("01", "02", "03", "04")
        ],
        _record(
            "05",
            "Cluster method",
            op="clusters_inspect",
            difficulty="intermediate",
            next_=["01", "02", "03", "04"],
        ),
    )
    items = next_methods_for(register, register.methods[4])
    assert len(items) == _NEXT_CAP == 3
    assert [item["method_id"] for item in items] == ["01", "02", "03"]


def test_skips_targets_without_a_run_hint() -> None:
    """A human-led target has no command to run, so it never rides."""
    register = _register(
        _record("01", "Led method"),
        _record("04", "Run method", op="duplicates_screen", difficulty="novice"),
        _record(
            "05",
            "Cluster method",
            op="clusters_inspect",
            difficulty="intermediate",
            next_=["04", "01"],
        ),
    )
    items = next_methods_for(register, register.methods[2])
    assert [item["method_id"] for item in items] == ["04"]


def test_items_carry_register_name_why_and_cli_hint() -> None:
    """The item fields copy the register; the run hint is the CLI path."""
    register = _register(
        _record("04", "Run method", op="duplicates_screen", difficulty="novice"),
        _record(
            "05",
            "Cluster method",
            op="clusters_inspect",
            difficulty="intermediate",
            next_=["04"],
        ),
    )
    items = next_methods_for(register, register.methods[1])
    item = items[0]
    assert set(item) == {"method_id", "title", "why", "run"}
    assert item["method_id"] == "04"
    assert item["title"] == "Run method"
    assert item["why"] == "Build on this method with Run method (novice level)."
    assert item["run"] == "corpus-kb duplicates-screen"


def test_why_drops_the_level_clause_when_difficulty_is_unset() -> None:
    """An unrated target states the edge without a level it cannot back."""
    register = _register(
        _record("04", "Run method", op="duplicates_screen"),
        _record("05", "Cluster method", op="clusters_inspect", next_=["04"]),
    )
    items = next_methods_for(register, register.methods[1])
    assert items[0]["why"] == "Build on this method with Run method."


def test_no_next_chain_is_an_empty_list() -> None:
    """A chosen row without ``next`` yields [] — never a made-up step."""
    register = _register(_record("05", "Quiet method", op="duplicates_screen"))
    assert next_methods_for(register, register.methods[0]) == []


def test_unrated_targets_sort_last() -> None:
    """An unrated difficulty carries no promise, so it rides last."""
    register = _register(
        _record("01", "Unrated method", op="duplicates_screen"),
        _record("04", "Rated method", op="duplicates_screen", difficulty="advanced"),
        _record("05", "Cluster method", op="clusters_inspect", next_=["04", "01"]),
    )
    items = next_methods_for(register, register.methods[2])
    assert [item["method_id"] for item in items] == ["04", "01"]


# -- The CLI hint source ----------------------------------------------------


def test_cli_hint_derives_from_the_surface_registry() -> None:
    """The hint is the registry's cli_path under the corpus-kb prefix."""
    assert cli_run_hint("clusters_inspect") == "corpus-kb clusters-inspect"
    assert cli_run_hint("duplicates_screen") == "corpus-kb duplicates-screen"


def test_cli_hint_fallback_maps_the_registry_gap() -> None:
    """ingest_text has no registry row; the one fallback row names it."""
    assert cli_run_hint("ingest_text") == "corpus-kb ingest-text"


def test_cli_hint_is_none_without_a_command() -> None:
    """No registry row and no fallback: None, never a guessed command."""
    assert cli_run_hint("no_such_op") is None
    assert cli_run_hint(None) is None


def test_every_implemented_register_op_yields_a_hint() -> None:
    """Drift guard: an op the registry misses cannot hide in a chain.

    The register's implemented ops and the registry's CLI rows must
    agree; a future op without a hint fails here, not in a novice's
    envelope.
    """
    register = load_method_register({})
    hinted = {row.op for row in register.methods if row.op and cli_run_hint(row.op)}
    implemented = {row.op for row in register.methods if row.op}
    assert hinted == implemented


# -- The key rides the shared handler on every outcome ----------------------


async def _run(
    question: str = QUESTION,
    method_id: str | None = None,
    config: dict[str, object] | None = _PGML,
) -> dict[str, object]:
    return await analysis_route(
        cast("asyncpg.Pool", None),  # routing reads no database
        config=config,
        question=question,
        method_id=method_id,
    )


async def test_resolved_degraded_envelope_holds_an_empty_list() -> None:
    """The live fixture route is degraded; an empty list stays honest.

    Method 29 authors no ``next`` chain, so even a resolved run would
    hold []; the degraded status holds [] regardless of the chain.
    """
    result = await _run()
    assert result["status"] == "degraded"
    assert result["next_methods"] == []


async def test_resolved_envelope_lists_the_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolved answer carries the chosen row's chain, easiest first.

    The split fake keeps the run healthy (no degradation), so this is
    the full-resolution case: the status is ``resolved``, not the
    degraded phrase path.
    """
    register = _register(
        _record("02", "A method", op="duplicates_screen", difficulty="novice"),
        _record("03", "B method", op="duplicates_screen", difficulty="advanced"),
        _record(
            "05",
            "Cluster method",
            [_TIE_A],
            op="clusters_inspect",
            difficulty="intermediate",
            next_=["03", "02"],
        ),
    )
    monkeypatch.setattr(
        "corpus_kb.tools.analysis_route_tools._router_embedder", lambda _cfg: _SplitEmbedder()
    )
    monkeypatch.setattr(
        "corpus_kb.tools.analysis_route_tools.load_method_register", lambda _cfg: register
    )
    result = await _run(question=_TIE_A, config={})
    assert result["status"] == "resolved"
    items = cast("list[dict[str, object]]", result["next_methods"])
    assert [item["method_id"] for item in items] == ["02", "03"]
    assert items[0]["run"] == "corpus-kb duplicates-screen"


async def test_ambiguous_envelope_holds_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No chosen method, no chain: [] rides the ladder answer."""
    register = _register(
        _record("01", "Codebook method", [_TIE_B], op="duplicates_screen"),
        _record(
            "04",
            "Run method",
            op="duplicates_screen",
            difficulty="novice",
        ),
        _record("05", "Cluster method", [_TIE_A], op="clusters_inspect", next_=["04"]),
    )
    monkeypatch.setattr(
        "corpus_kb.tools.analysis_route_tools._router_embedder", lambda _cfg: _FlatEmbedder()
    )
    monkeypatch.setattr(
        "corpus_kb.tools.analysis_route_tools.load_method_register", lambda _cfg: register
    )
    result = await _run(question=f"{_TIE_A} {_TIE_B}", config={})
    assert result["status"] == "ambiguous"
    assert result["next_methods"] == []


async def test_override_envelope_carries_the_override_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An analyst override hand-holds from the override row's chain."""
    register = _register(
        _record(
            "01",
            "Codebook method",
            [_TIE_B],
            op="duplicates_screen",
            difficulty="novice",
            next_=["02"],
        ),
        _record("02", "Next method", op="duplicates_screen", difficulty="intermediate"),
        _record("05", "Cluster method", [_TIE_A], op="clusters_inspect", next_=["04"]),
        _record("04", "Led method", op="duplicates_screen", difficulty="novice"),
    )
    monkeypatch.setattr(
        "corpus_kb.tools.analysis_route_tools._router_embedder", lambda _cfg: _FlatEmbedder()
    )
    monkeypatch.setattr(
        "corpus_kb.tools.analysis_route_tools.load_method_register", lambda _cfg: register
    )
    result = await _run(question=f"{_TIE_A} {_TIE_B}", method_id="01", config={})
    intent = cast("dict[str, object]", result["intent"])
    assert intent["note"] == "expert_override"
    items = cast("list[dict[str, object]]", result["next_methods"])
    assert [item["method_id"] for item in items] == ["02"]
    assert items[0]["run"] == "corpus-kb duplicates-screen"


async def test_repeat_run_is_byte_identical_with_a_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same question, same register, same bytes — chain included."""
    register = _register(
        _record(
            "05",
            "Cluster method",
            [_TIE_A, "Which cluster structure fits these records?"],
            op="clusters_inspect",
            difficulty="intermediate",
            next_=["02", "03", "04"],
        ),
        _record("02", "A method", op="duplicates_screen", difficulty="novice"),
        _record("03", "B method", op="duplicates_screen", difficulty="intermediate"),
        _record("04", "C method", op="duplicates_screen", difficulty="advanced"),
    )
    monkeypatch.setattr(
        "corpus_kb.tools.analysis_route_tools._router_embedder", lambda _cfg: _SplitEmbedder()
    )
    monkeypatch.setattr(
        "corpus_kb.tools.analysis_route_tools.load_method_register", lambda _cfg: register
    )
    first = json.dumps(await _run(question=_TIE_A, config={}), sort_keys=True)
    second = json.dumps(await _run(question=_TIE_A, config={}), sort_keys=True)
    assert first == second


# -- The live register stays inside the plain-English caps ------------------


def test_live_chains_stay_under_twenty_words() -> None:
    """Every live register chain speaks in one sentence of 20 words."""
    register = load_method_register({})
    for row in register.methods:
        for item in next_methods_for(register, row):
            why = str(item["why"])
            words = len(why.split())
            assert words <= 20, f"{row.id} -> {item['method_id']}: {words} words"
            assert why.count(".") == 1, f"{row.id} -> {item['method_id']}: one sentence only"


def test_live_chains_order_by_difficulty_then_id() -> None:
    """The live register's chains match the derivation's stated order."""
    register = load_method_register({})
    levels = {"novice": 0, "intermediate": 1, "advanced": 2}
    by_id = {row.id: row for row in register.methods}
    for row in register.methods:
        chain = row.next or []
        expected = sorted(chain, key=lambda mid: (levels.get(by_id[mid].difficulty or "", 9), mid))
        assert chain == expected, f"{row.id}: authored order {chain} vs derivation order {expected}"
