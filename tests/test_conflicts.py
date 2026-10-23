"""Tests for the contradiction cascade (Living Corpus W3): gates, scan
driver, and the typed validation on the resolve handler."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from corpus_kb.conflicts import (
    CascadeVerdict,
    StatementRow,
    entity_swap,
    gate_a,
    gate_b,
    relation_swap,
    scan_statements,
)
from corpus_kb.errors import ValidationError


def _row(
    statement_id: str,
    text: str,
    *,
    valid_at: datetime | None = None,
    observed_at: datetime | None = None,
    entities: tuple[str, ...] = ("ice cream",),
    template_id: str = "preference",
    superseded_by: str | None = None,
) -> StatementRow:
    return StatementRow(
        statement_id=statement_id,
        text=text,
        template_id=template_id,
        entity_names=entities,
        valid_at=valid_at,
        observed_at=observed_at or datetime(2026, 1, 1, tzinfo=UTC),
        superseded_by=superseded_by,
    )


def test_gate_a_non_overlapping_windows_is_a_revision() -> None:
    a = _row("a", "I like ice cream.", observed_at=datetime(2020, 1, 1, tzinfo=UTC))
    b = _row("b", "I still like ice cream.", observed_at=datetime(2026, 1, 1, tzinfo=UTC))
    verdict = gate_a(a, b, "ice cream")
    assert verdict is not None
    assert verdict.verdict == "revision"
    assert verdict.kind == "temporal"
    assert verdict.gate_reached == "A"


def test_gate_b_relation_swap_conflicts() -> None:
    a = _row("a", "I like ice cream.")
    b = _row("b", "I dislike ice cream.")
    assert relation_swap(a, b)
    verdict = gate_b(a, b, "ice cream")
    assert verdict is not None
    assert verdict.kind == "relation_swap"
    assert verdict.verdict == "conflict"


def test_gate_b_entity_swap_conflicts() -> None:
    a = _row("a", "Elena works at Acme.", entities=("Elena", "Acme"))
    b = _row("b", "Elena works at Beta.", entities=("Elena", "Beta"))
    assert entity_swap(a, b)
    verdict = gate_b(a, b, "Elena")
    assert verdict is not None
    assert verdict.kind == "entity_swap"


def test_agreeing_statements_pass_every_gate() -> None:
    a = _row("a", "I like ice cream.")
    b = _row("b", "I really like ice cream a lot.")
    assert gate_a(a, b, "ice cream") is None
    assert gate_b(a, b, "ice cream") is None


@pytest.mark.asyncio
async def test_scan_flags_revision_without_nli() -> None:
    raw = [
        {
            "statement_id": "a",
            "text": "I like ice cream.",
            "template_id": "preference",
            "entity_names": ["ice cream"],
            "valid_at": None,
            "observed_at": datetime(2020, 1, 1, tzinfo=UTC),
            "superseded_by": None,
        },
        {
            "statement_id": "b",
            "text": "I like ice cream.",
            "template_id": "preference",
            "entity_names": ["ice cream"],
            "valid_at": None,
            "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
            "superseded_by": None,
        },
    ]
    verdicts = await scan_statements(raw)
    assert [v.verdict for v in verdicts] == ["revision"]
    assert verdicts[0].statement_a_id == "a"
    assert verdicts[0].statement_b_id == "b"


@pytest.mark.asyncio
async def test_scan_uses_the_nli_judge_for_gate_c() -> None:
    raw = [
        {
            "statement_id": "a",
            "text": "The lake is frozen solid.",
            "template_id": "statement",
            "entity_names": ["lake"],
            "valid_at": None,
            "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
            "superseded_by": None,
        },
        {
            "statement_id": "b",
            "text": "The lake is warm enough for swimming.",
            "template_id": "statement",
            "entity_names": ["lake"],
            "valid_at": None,
            "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
            "superseded_by": None,
        },
    ]

    async def nli(a_text: str, b_text: str) -> str | None:
        return "CONTRADICT"

    verdicts = await scan_statements(raw, nli=nli)
    assert len(verdicts) == 1
    assert verdicts[0].gate_reached == "C"
    assert verdicts[0].verdict == "conflict"
    assert verdicts[0].nli_verdict == "CONTRADICT"


@pytest.mark.asyncio
async def test_scan_excludes_superseded_rows() -> None:
    raw = [
        {
            "statement_id": "old",
            "text": "I like ice cream.",
            "template_id": "preference",
            "entity_names": ["ice cream"],
            "valid_at": None,
            "observed_at": datetime(2020, 1, 1, tzinfo=UTC),
            "superseded_by": "new",
        },
        {
            "statement_id": "new",
            "text": "I dislike ice cream.",
            "template_id": "preference",
            "entity_names": ["ice cream"],
            "valid_at": None,
            "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
            "superseded_by": None,
        },
    ]
    assert await scan_statements(raw) == []


@pytest.mark.asyncio
async def test_resolve_handler_rejects_bad_verdict() -> None:
    from corpus_kb.tools.conflicts_tools import conflicts_resolve

    with pytest.raises(ValidationError, match="conflict, revision, or dismissed"):
        await conflicts_resolve(None, None, resolved_verdict="maybe", resolved_by="r")


def test_cascade_verdict_is_frozen() -> None:
    verdict = CascadeVerdict("a", "b", "x", "temporal", "A", "revision")
    with pytest.raises(AttributeError):
        verdict.verdict = "conflict"  # type: ignore[misc]
