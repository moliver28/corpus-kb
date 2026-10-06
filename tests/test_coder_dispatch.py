"""Test coder dispatch: output validation and routing."""

from __future__ import annotations

from corpus_kb.coding.coder_dispatch import (
    CoderWindowOutput,
    validate_coder_output,
)


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
