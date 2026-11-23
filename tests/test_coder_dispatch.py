"""Test coder dispatch: output validation, routing, and the window contract."""

from __future__ import annotations

import pytest

from corpus_kb.chunking.hierarchy import estimate_tokens
from corpus_kb.coding.coder_dispatch import (
    CoderWindowOutput,
    validate_coder_output,
)
from corpus_kb.coding.coder_window import build_window

_CODE_ROW = {
    "code_id": "CTRL-001",
    "brief_definition": "Control activity the participant reports.",
    "inclusion_criteria": "Participant mentions a control and its use.",
    "exclusion_criteria": "Hypothetical or planned controls only.",
}


def test_coder_window_output_dataclass() -> None:
    """Test CoderWindowOutput creation and field defaults."""
    output = CoderWindowOutput(
        decision="assign", confidence=0.95, evidence_quote="test quote", rationale="test rationale"
    )

    assert output.decision == "assign"
    assert output.confidence == 0.95
    assert output.evidence_quote == "test quote"
    assert output.rationale == "test rationale"
    assert output.logprobs is None


def test_coder_window_output_with_logprobs() -> None:
    """Test CoderWindowOutput with logprobs."""
    logprobs_data = [{"token": "assign", "log": -0.5}, {"token": "reject", "log": -2.3}]

    output = CoderWindowOutput(
        decision="assign",
        confidence=0.85,
        evidence_quote="quote",
        rationale="rationale",
        logprobs=logprobs_data,
    )

    assert output.logprobs == logprobs_data
    assert len(output.logprobs) == 2


def test_validate_coder_output_valid_assign() -> None:
    """Test validation of valid assign decision."""
    output = CoderWindowOutput(
        decision="assign",
        confidence=0.95,
        evidence_quote="participant said this",
        rationale="clear mention",
    )

    is_valid, msg = validate_coder_output(output)
    assert is_valid is True
    assert msg == ""


def test_validate_coder_output_valid_reject() -> None:
    """Test validation of valid reject decision."""
    output = CoderWindowOutput(
        decision="reject", confidence=0.88, evidence_quote=None, rationale="does not meet criteria"
    )

    is_valid, msg = validate_coder_output(output)
    assert is_valid is True
    assert msg == ""


def test_validate_coder_output_invalid_decision() -> None:
    """Test validation rejects invalid decision."""
    output = CoderWindowOutput(
        decision="maybe", confidence=0.50, evidence_quote="quote", rationale="rationale"
    )

    is_valid, msg = validate_coder_output(output)
    assert is_valid is False
    assert "Invalid decision" in msg
    assert "maybe" in msg


def test_validate_coder_output_confidence_too_high() -> None:
    """Test validation rejects confidence > 1.0."""
    output = CoderWindowOutput(
        decision="assign", confidence=1.5, evidence_quote="quote", rationale="rationale"
    )

    is_valid, msg = validate_coder_output(output)
    assert is_valid is False
    assert "Confidence out of range" in msg


def test_validate_coder_output_confidence_too_low() -> None:
    """Test validation rejects confidence < 0.0."""
    output = CoderWindowOutput(
        decision="assign", confidence=-0.1, evidence_quote="quote", rationale="rationale"
    )

    is_valid, msg = validate_coder_output(output)
    assert is_valid is False
    assert "Confidence out of range" in msg


def test_validate_coder_output_confidence_boundaries() -> None:
    """Test validation accepts confidence at 0.0 and 1.0."""
    # Confidence exactly 0.0
    output_zero = CoderWindowOutput(
        decision="assign", confidence=0.0, evidence_quote="quote", rationale="rationale"
    )
    is_valid, _msg = validate_coder_output(output_zero)
    assert is_valid is True

    # Confidence exactly 1.0
    output_one = CoderWindowOutput(
        decision="assign", confidence=1.0, evidence_quote="quote", rationale="rationale"
    )
    is_valid, _msg = validate_coder_output(output_one)
    assert is_valid is True


def test_validate_coder_output_assign_missing_quote() -> None:
    """Test validation requires quote for assign decision."""
    output = CoderWindowOutput(
        decision="assign", confidence=0.95, evidence_quote=None, rationale="rationale"
    )

    is_valid, msg = validate_coder_output(output)
    assert is_valid is False
    assert "Evidence quote required" in msg


def test_validate_coder_output_assign_empty_quote() -> None:
    """Test validation treats empty string as missing quote."""
    output = CoderWindowOutput(
        decision="assign", confidence=0.95, evidence_quote="", rationale="rationale"
    )

    # Empty string is falsy, so an assign without a quote must fail validation.
    is_valid, _ = validate_coder_output(output)
    assert is_valid is False


def test_validate_coder_output_reject_ignores_quote() -> None:
    """Test validation does not require quote for reject decision."""
    # Reject with quote is fine
    output_with_quote = CoderWindowOutput(
        decision="reject", confidence=0.90, evidence_quote="some quote", rationale="rationale"
    )
    is_valid, _msg = validate_coder_output(output_with_quote)
    assert is_valid is True

    # Reject without quote is also fine
    output_no_quote = CoderWindowOutput(
        decision="reject", confidence=0.90, evidence_quote=None, rationale="rationale"
    )
    is_valid, _msg = validate_coder_output(output_no_quote)
    assert is_valid is True


def test_build_window_truncates_oversized_chunk() -> None:
    """Contract item 5: a chunk over the ceiling is cut, window fits the ceiling."""
    window = build_window("word " * 20_000, [_CODE_ROW], max_tokens=2_000)

    assert estimate_tokens(window) <= 2_000
    assert "[truncated]" in window
    assert "CTRL-001" in window  # the candidate code block survives the cut


def test_build_window_keeps_anchor_when_truncating() -> None:
    """Contract item 5: the anchor (output contract + worked example) survives."""
    window = build_window("word " * 20_000, [_CODE_ROW], max_tokens=2_000)

    assert '{"decisions":' in window  # output contract shape still rendered
    assert "Worked example" in window  # the anchor example is never cut


def test_build_window_context_yields_to_chunk() -> None:
    """Chained context is cut before the target chunk: evidence needs the chunk."""
    chunk = "participant turn " * 50
    context = "moderator question " * 5_000
    window = build_window(chunk, [_CODE_ROW], qa_context=context, max_tokens=2_000)

    assert "participant turn" in window  # target chunk text intact
    assert estimate_tokens(window) <= 2_000
    assert "[truncated]" in window  # the context was the part that gave way


def test_build_window_under_ceiling_is_untouched() -> None:
    """A window that fits carries no truncation marker and keeps the full chunk."""
    window = build_window("a short turn", [_CODE_ROW])

    assert "[truncated]" not in window
    assert "a short turn" in window


def test_build_window_rejects_impossible_ceiling() -> None:
    """A ceiling below the fixed scaffolding raises instead of emitting junk."""
    with pytest.raises(ValueError, match="ceiling"):
        build_window("any turn", [_CODE_ROW], max_tokens=10)
