"""U43 structured-output canary + validate/repair (coding/structured_output)."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from corpus_kb.coding.structured_output import (
    CANARY_SCHEMA,
    REPAIR_RETRIES_DEFAULT,
    CanaryResult,
    StructuredOutcome,
    enforcement_blocks,
    run_canary,
    structured_call,
    validate_against_schema,
)
from corpus_kb.research.release.structured_stats import StructuredOutputStats

Schema = dict[str, object]

SCHEMA: Schema = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "enum": ["billing", "shipping", "other"]},
        "confidence": {"type": "number"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["topic"],
    "additionalProperties": False,
}


def test_validator_accepts_valid_and_reports_errors():
    good = {"topic": "billing", "confidence": 0.9, "tags": ["a"]}
    assert validate_against_schema(good, SCHEMA) == []
    missing = validate_against_schema({}, SCHEMA)
    assert any("required field missing" in e for e in missing)
    extra = validate_against_schema({"topic": "billing", "nope": 1}, SCHEMA)
    assert any("additional property not allowed" in e for e in extra)
    bad_enum = validate_against_schema({"topic": "BANANA"}, SCHEMA)
    assert any("not in enum" in e for e in bad_enum)
    bad_type = validate_against_schema({"topic": 3}, SCHEMA)
    assert any("expected string" in e for e in bad_type)


def test_validator_rejects_pattern_and_non_flat_recursion_is_bounded():
    with pytest.raises(ValueError, match="pattern"):
        validate_against_schema({}, {"type": "object", "properties": {"p": {"pattern": "x"}}})
    assert validate_against_schema({"topic": "other"}, CANARY_SCHEMA) == []


def _caller(reply: str) -> Callable[[str, str], str]:
    def call(_prompt: str, _model: str) -> str:
        return reply

    return call


def _failing_then(reply: str) -> Callable[[str, str], str]:
    state = {"n": 0}

    def call(_prompt: str, _model: str) -> str:
        state["n"] += 1
        return "not json" if state["n"] == 1 else reply

    return call


def test_canary_paths():
    enforced = run_canary("m", _caller('{"topic": "other"}'))
    assert enforced.status == "enforced"
    unparseable = run_canary("m", _caller('{"topic": "BANANA"}'))
    assert unparseable.status == "not_enforced"
    assert "BANANA" in unparseable.raw_reply
    prose = run_canary("m", _caller("I cannot do that"))
    assert prose.status == "not_enforced"

    def transport(_prompt: str, _model: str) -> str:
        raise ConnectionError("down")

    unknown = run_canary("m", transport)
    assert unknown.status == "unknown"
    assert isinstance(unknown, CanaryResult)


def test_structured_call_first_attempt_valid():
    outcome = structured_call("prompt", SCHEMA, "m", _caller('{"topic": "billing"}'))
    assert outcome.status == "valid"
    assert outcome.attempts == 1
    assert outcome.first_attempt_valid and not outcome.repair_recovered
    assert outcome.data == {"topic": "billing"}


def test_structured_call_repairs_then_recovers():
    outcome = structured_call("prompt", SCHEMA, "m", _failing_then('{"topic": "other"}'))
    assert outcome.status == "valid"
    assert outcome.attempts == 2
    assert outcome.repair_recovered


def test_structured_call_never_coerces_after_retries_exhausted():
    assert REPAIR_RETRIES_DEFAULT == 1

    def always_bad(_prompt: str, _model: str) -> str:
        return '{"topic": "BANANA"}'

    outcome = structured_call("prompt", SCHEMA, "m", always_bad)
    assert outcome.status == "invalid_output"
    assert outcome.data is None
    assert outcome.attempts == REPAIR_RETRIES_DEFAULT + 1


def test_structured_call_transport_error_is_honest():
    def down(_prompt: str, _model: str) -> str:
        raise ConnectionError("refused")

    outcome = structured_call("prompt", SCHEMA, "m", down)
    assert outcome.status == "transport_error"
    assert outcome.data is None


def test_stats_rates():
    stats = StructuredOutputStats()
    stats.record(StructuredOutcome(model="m", data={}, status="valid", attempts=1))
    stats.record(StructuredOutcome(model="m", data={}, status="valid", attempts=2))
    stats.record(StructuredOutcome(model="m", data=None, status="invalid_output", attempts=2))
    report = stats.to_report()["m"]
    assert report["attempts"] == 3
    assert report["first_attempt_valid_rate"] == pytest.approx(1 / 3)
    assert report["repair_recovery_rate"] == pytest.approx(1 / 3)
    assert report["invalid_output"] == 1


def test_enforcement_blocks_only_in_strict_mode():
    assert enforcement_blocks("not_enforced", require_enforcement=True)
    assert not enforcement_blocks("not_enforced", require_enforcement=False)
    assert not enforcement_blocks("enforced", require_enforcement=True)
    assert not enforcement_blocks("unknown", require_enforcement=True)


def test_canary_prompt_is_adversarial_against_its_schema():
    decoded = json.loads(  # the prompt's requested output violates the schema
        json.dumps({"topic": "BANANA"})
    )
    assert validate_against_schema(decoded, CANARY_SCHEMA)
