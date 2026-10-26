"""Survey strategy (unified-ingestion Wave 5): detection, chunks, spans, wiring.

Offline: Q/A header detection (and its non-claims), one chunk per answer
with the question carried in metadata, exact char spans on the canonical
document text, budget splits, file sources, and the registry/config-map
seams. Everything runs without a database or an embedder.
"""

from __future__ import annotations

from pathlib import Path

from corpus_kb.ingestion.base import (
    DETECT_ADOPT_CONFIDENCE,
    TOKEN_BUDGET_CHARS,
    SourceProfile,
    validate_plan,
)
from corpus_kb.ingestion.registry import resolve_strategy, strategy_names
from corpus_kb.ingestion.strategies.survey import SURVEY_STRATEGY_NAME, SurveyStrategy

CFG_EMPTY: dict[str, object] = {"ingestion": {"strategies": {}, "default": "default"}}
CFG_SURVEY_MAP: dict[str, object] = {
    "ingestion": {"strategies": {"survey": "survey"}, "default": "default"}
}

SURVEY_CSV = (
    "question,answer\n"
    "What is RAG?,Retrieval-augmented generation.\n"
    "Why chunk?,Chunking bounds the token budget per unit.\n"
)


def _profile(path: str = "responses.csv", is_file: bool = False) -> SourceProfile:
    return SourceProfile(source_type="text", is_file=is_file, path=path)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detect_qa_header_adopts() -> None:
    result = SurveyStrategy().detect("text", "responses.csv", SURVEY_CSV)
    assert result.confidence >= DETECT_ADOPT_CONFIDENCE
    assert result.reason == "csv-qa-header"


def test_detect_plain_data_csv_stays_below_adopt() -> None:
    result = SurveyStrategy().detect("text", "table.csv", "name,value\nalpha,1\n")
    assert result.reason == "csv-suffix"
    assert result.confidence < DETECT_ADOPT_CONFIDENCE


def test_detect_speaker_csv_belongs_to_transcript() -> None:
    from corpus_kb.ingestion.strategies.transcript import TranscriptStrategy

    head = "speaker,text\nalice,hello world\n"
    assert SurveyStrategy().detect(None, "data.csv", head).confidence < DETECT_ADOPT_CONFIDENCE
    assert TranscriptStrategy().detect(None, "data.csv", head).confidence >= DETECT_ADOPT_CONFIDENCE


def test_detect_non_csv_is_zero() -> None:
    assert SurveyStrategy().detect("text", "notes.txt", SURVEY_CSV).confidence == 0.0


def test_detect_empty_head_is_suffix_only() -> None:
    result = SurveyStrategy().detect(None, "rows.csv", "")
    assert result.reason == "csv-suffix"
    assert result.confidence < DETECT_ADOPT_CONFIDENCE


# ---------------------------------------------------------------------------
# Chunk building
# ---------------------------------------------------------------------------


def test_build_chunks_one_per_row_with_question_context() -> None:
    plan = SurveyStrategy().build_chunks(SURVEY_CSV, "responses.csv", _profile())
    assert len(plan.chunks) == 2
    first = plan.chunks[0]
    assert first.text == "Q: What is RAG?\nA: Retrieval-augmented generation."
    assert first.metadata["strategy"] == "survey"
    assert first.metadata["question"] == "What is RAG?"
    assert first.metadata["row"] == 2
    assert first.metadata["answer_column"] == "answer"
    assert validate_plan(plan) == []


def test_build_chunks_exact_char_spans_on_document_text() -> None:
    plan = SurveyStrategy().build_chunks(SURVEY_CSV, "responses.csv", _profile())
    for chunk in plan.chunks:
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text


def test_build_chunks_multiple_answer_columns() -> None:
    csv_text = "question,answer,response\nQ1?,alpha one,alpha two\n"
    plan = SurveyStrategy().build_chunks(csv_text, "s.csv", _profile())
    assert [c.metadata["answer_column"] for c in plan.chunks] == ["answer", "response"]
    assert all(c.metadata["question"] == "Q1?" for c in plan.chunks)
    for chunk in plan.chunks:
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text


def test_build_chunks_missing_columns_is_empty_but_conformant() -> None:
    plan = SurveyStrategy().build_chunks("name,value\na,1\n", "t.csv", _profile())
    assert plan.chunks == []
    assert plan.empty_reason == "no question and answer columns found in the header"
    assert validate_plan(plan) == []


def test_build_chunks_no_data_rows_is_empty_but_conformant() -> None:
    plan = SurveyStrategy().build_chunks("question,answer\n", "empty.csv", _profile())
    assert plan.chunks == []
    assert plan.empty_reason == "no question/answer rows recovered from source"
    assert validate_plan(plan) == []


def test_oversized_answer_splits_with_exact_spans() -> None:
    answer = "word " * (TOKEN_BUDGET_CHARS // 2)
    csv_text = f"question,answer\nBig?, {answer}\n"
    plan = SurveyStrategy().build_chunks(csv_text, "big.csv", _profile())
    assert len(plan.chunks) > 1
    assert any("split" in warning for warning in plan.warnings)
    for chunk in plan.chunks:
        assert len(chunk.text) <= TOKEN_BUDGET_CHARS
        assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
    assert validate_plan(plan) == []


def test_blank_cells_are_skipped() -> None:
    csv_text = "question,answer\nQ1?,\n,Q2 answer\nQ2?,kept\n"
    plan = SurveyStrategy().build_chunks(csv_text, "gaps.csv", _profile())
    assert [c.metadata["row"] for c in plan.chunks] == [4]
    assert plan.chunks[0].text == "Q: Q2?\nA: kept"


def test_file_source_reads_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "survey.csv"
    path.write_text(SURVEY_CSV, encoding="utf-8")
    plan = SurveyStrategy().build_chunks(
        "", "survey.csv", SourceProfile(source_type="text", is_file=True, path=str(path))
    )
    assert len(plan.chunks) == 2
    assert validate_plan(plan) == []


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_strategy_is_registered() -> None:
    assert SURVEY_STRATEGY_NAME in strategy_names()


def test_resolution_detection_adopts_survey_csv() -> None:
    resolution = resolve_strategy("text", "responses.csv", SURVEY_CSV, CFG_EMPTY)
    assert resolution.strategy.name == "survey"
    assert resolution.basis == "detection"
    assert resolution.detected == "survey"


def test_resolution_config_map_and_explicit_override() -> None:
    mapped = resolve_strategy("survey", "x.txt", "plain", CFG_SURVEY_MAP)
    assert mapped.strategy.name == "survey"
    assert mapped.basis == "config"
    explicit = resolve_strategy("text", "x.txt", "plain", CFG_EMPTY, strategy_override="survey")
    assert explicit.strategy.name == "survey"
    assert explicit.basis == "explicit"
