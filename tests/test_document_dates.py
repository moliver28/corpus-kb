"""Unit tests for the WP-1 date resolver and plan lift (analysis/dates.py).

No database: the guard, the preference order, and the plan-level lift are
pure functions. The live backfill and the forward upsert live in
``tests/test_migration_027.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from corpus_kb.analysis.dates import (
    BACKFILL_RULE_VERSION,
    parse_date_value,
    plan_document_date,
    resolve_document_date,
)

MAY_1 = datetime(2024, 5, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# parse_date_value: the regex-guarded ISO parse
# ---------------------------------------------------------------------------


def test_valid_iso_values_parse_as_utc_when_naive() -> None:
    assert parse_date_value("2024-05-01") == MAY_1
    assert parse_date_value("2024-05-01T09:30:00") == datetime(2024, 5, 1, 9, 30, tzinfo=UTC)
    assert parse_date_value("2024-05-01T09:30:00.5") == datetime(
        2024, 5, 1, 9, 30, 0, 500000, tzinfo=UTC
    )


def test_valid_iso_values_keep_explicit_offsets() -> None:
    assert parse_date_value("2024-05-01T09:30:00Z") == datetime(2024, 5, 1, 9, 30, tzinfo=UTC)
    assert parse_date_value("2024-05-01T09:30:00+02:00") == datetime(2024, 5, 1, 7, 30, tzinfo=UTC)


def test_malformed_values_return_none() -> None:
    assert parse_date_value("coming soon") is None
    assert parse_date_value("2024-13-01") is None
    assert parse_date_value("2024-02-30") is None
    assert parse_date_value("2024-1-15") is None
    assert parse_date_value("15/01/2024") is None
    assert parse_date_value("2024-05-01 and later prose") is None
    assert parse_date_value("  ") is None


def test_absent_or_non_text_values_return_none() -> None:
    assert parse_date_value(None) is None
    assert parse_date_value(20240501) is None
    assert parse_date_value(["2024-05-01"]) is None
    assert parse_date_value({"date": "2024-05-01"}) is None


# ---------------------------------------------------------------------------
# resolve_document_date: published_time first, then modified_time
# ---------------------------------------------------------------------------


def test_resolve_prefers_published_over_modified() -> None:
    meta: dict[str, object] = {"published_time": "2024-05-01", "modified_time": "2024-09-09"}
    assert resolve_document_date(meta) == MAY_1


def test_resolve_falls_back_to_modified() -> None:
    meta: dict[str, object] = {"modified_time": "2024-09-09T10:00:00Z"}
    assert resolve_document_date(meta) == datetime(2024, 9, 9, 10, tzinfo=UTC)


def test_resolve_missing_or_malformed_keys_return_none() -> None:
    assert resolve_document_date({}) is None
    assert resolve_document_date({"published_time": None, "modified_time": None}) is None
    assert resolve_document_date({"published_time": "coming soon", "modified_time": "soon"}) is None
    assert resolve_document_date({"unrelated": "2024-05-01"}) is None


def test_resolve_malformed_published_does_not_block_modified() -> None:
    meta: dict[str, object] = {"published_time": "coming soon", "modified_time": "2024-09-09"}
    assert resolve_document_date(meta) == datetime(2024, 9, 9, tzinfo=UTC)


# ---------------------------------------------------------------------------
# plan_document_date: the forward-ingest lift across chunk metadata
# ---------------------------------------------------------------------------


def test_plan_lift_reads_web_metadata() -> None:
    chunks: list[dict[str, object]] = [
        {"published_time": "2024-05-01T10:00:00Z"},
        {"published_time": "2024-05-01T10:00:00Z", "modified_time": "2024-06-01"},
    ]
    assert plan_document_date(chunks) == datetime(2024, 5, 1, 10, tzinfo=UTC)


def test_plan_lift_reads_pptx_core_properties() -> None:
    deck: list[dict[str, object]] = [
        {"created": "2023-05-01T08:00:00", "modified": "2023-06-01T00:00:00"},
        {"created": "2023-05-01T08:00:00", "modified": "2023-06-01T00:00:00"},
    ]
    assert plan_document_date(deck) == datetime(2023, 5, 1, 8, tzinfo=UTC)


def test_plan_lift_takes_the_earliest_declared_date() -> None:
    chunks: list[dict[str, object]] = [
        {"published_time": "2024-05-01"},
        {"published_time": "2024-01-01T00:00:00Z"},
    ]
    assert plan_document_date(chunks) == datetime(2024, 1, 1, tzinfo=UTC)


def test_plan_lift_absent_everywhere_is_none() -> None:
    assert plan_document_date([]) is None
    assert plan_document_date([{}, {"qa_context": {"question": "when?"}}]) is None


# ---------------------------------------------------------------------------
# rule version pin
# ---------------------------------------------------------------------------


def test_backfill_rule_version_is_pinned() -> None:
    assert BACKFILL_RULE_VERSION == 1
