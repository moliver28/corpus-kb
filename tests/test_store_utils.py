"""Unit tests for the shared store row helpers (storage.store_utils)."""

from __future__ import annotations

from datetime import UTC, datetime

from corpus_kb.storage.store_utils import entity_names, iso_or_none, parse_jsonb


def test_parse_jsonb_parses_a_json_string() -> None:
    assert parse_jsonb('[{"name": "ice cream"}]') == [{"name": "ice cream"}]


def test_parse_jsonb_keeps_non_string_values_raw() -> None:
    value: list[dict[str, str]] = [{"name": "x"}]
    assert parse_jsonb(value) is value
    assert parse_jsonb(None) is None


def test_parse_jsonb_keeps_a_malformed_string_raw() -> None:
    assert parse_jsonb("{not json") == "{not json"


def test_entity_names_unfolds_a_json_string_of_dicts() -> None:
    raw = '[{"name": "ice cream", "entity_type": "FOOD"}, {"entity_type": "FOOD"}]'
    assert entity_names(raw) == ["ice cream", ""]


def test_entity_names_reads_an_already_parsed_list() -> None:
    raw = [{"name": "lake"}, {"name": "boat"}]
    assert entity_names(raw) == ["lake", "boat"]


def test_entity_names_answers_empty_for_non_list_values() -> None:
    assert entity_names(None) == []
    assert entity_names(7) == []


def test_iso_or_none_formats_a_timestamp() -> None:
    moment = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert iso_or_none(moment) == "2026-10-31T12:00:00+00:00"


def test_iso_or_none_answers_none_for_null() -> None:
    assert iso_or_none(None) is None
