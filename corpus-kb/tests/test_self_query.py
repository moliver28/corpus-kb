"""TDD tests for self-query parsing and predicate-to-SQL building."""

from __future__ import annotations

from src.rag.self_query import ParsedQuery, Predicate, build_filter_sql, SelfQueryParser


def test_build_filter_sql_no_predicates_is_empty() -> None:
    frag, params = build_filter_sql([], start_index=4)
    assert frag == ""
    assert params == []


def test_build_filter_sql_numbers_from_start_index() -> None:
    predicates = [Predicate(target="documents.source_type", op="=", value="pdf")]
    frag, params = build_filter_sql(predicates, start_index=4)
    assert " AND documents.source_type = $4" in frag
    assert params == ["pdf"]


def test_build_filter_sql_rejects_non_whitelisted_column() -> None:
    predicates = [Predicate(target="documents.secret_column", op="=", value="x")]
    frag, params = build_filter_sql(predicates, start_index=4)
    assert frag == ""
    assert params == []


def test_build_filter_sql_tag_predicate_emits_exists() -> None:
    predicates = [Predicate(target="tag", op="=", value="urgent")]
    frag, params = build_filter_sql(predicates, start_index=4)
    assert "EXISTS (SELECT 1 FROM document_tags dt JOIN tags t" in frag
    assert params == ["urgent"]


def test_parser_falls_back_on_client_error(monkeypatch) -> None:
    parser = SelfQueryParser({"search": {"self_query": {"timeout_seconds": 1}}})

    def _raise(*_a: object, **_k: object) -> None:
        raise ConnectionError("no ollama")

    monkeypatch.setattr(parser, "_client_chat", _raise)
    result = parser.parse("pdf reports from last week")
    assert result == ParsedQuery(semantic_query="pdf reports from last week", predicates=[])
