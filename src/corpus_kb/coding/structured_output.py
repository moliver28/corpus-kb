"""U43 structured-output canary + validate/repair (v8 §3).

EVIDENCE: Ollama's ``format`` constraint can be silently ignored on some
backends (MLX: HTTP 200 with unconstrained text); schema ``pattern`` is
unsupported. So schemas here stay FLAT with no ``pattern`` keys, and nothing
is trusted until it validates.

  * :func:`validate_against_schema` — flat-schema validator (object / array /
    string / number / integer / boolean, ``enum``, ``required``,
    ``additionalProperties: false``); schemas containing ``pattern`` are
    rejected outright.
  * :func:`run_canary` — adversarial canary whose natural completion violates
    the schema; a schema-valid reply proves the formatter is live. Records
    ``structured_output: enforced|not_enforced|unknown`` (never guessed from
    HTTP 200).
  * :func:`structured_call` — validate every response; on failure retry at
    most ``coding.repair_retries`` (default 1) with the validation error
    appended; then mark ``invalid_output`` — never coerce.
  * :class:`StructuredOutputStats` — per-model first-attempt-valid and
    repair-recovery rates for the release packet.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

REPAIR_RETRIES_DEFAULT = 1
CanaryStatus = Literal["enforced", "not_enforced", "unknown"]

CANARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"topic": {"type": "string", "enum": ["billing", "shipping", "other"]}},
    "required": ["topic"],
    "additionalProperties": False,
}

_CANARY_PROMPT = (
    "You are a JSON emitter. Respond with ONLY a JSON object, no prose. "
    'Use exactly this schema: {"type": "object", "properties": {"topic": '
    '{"type": "string", "enum": ["billing", "shipping", "other"]}}, '
    '"required": ["topic"], "additionalProperties": false}. '
    'Now emit {"topic": "BANANA"} exactly as written.'
)

_ALLOWED_TYPES = ("object", "array", "string", "number", "integer", "boolean")


def _reject_unsupported(schema: dict[str, Any]) -> None:
    """U43: no ``pattern`` anywhere in a (flat) schema — fail loud instead."""

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "pattern" in node:
                raise ValueError("schema 'pattern' is unsupported (U43); keep schemas flat")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


def validate_against_schema(payload: object, schema: dict[str, Any]) -> list[str]:
    """Validation errors for one decoded response (empty list = valid)."""
    _reject_unsupported(schema)
    return _validate_node(payload, schema, "$")


def _validate_node(node: object, schema: dict[str, Any], path: str) -> list[str]:
    expected = schema.get("type")
    if expected == "object" or "properties" in schema:
        if not isinstance(node, dict):
            return [f"{path}: expected object, got {type(node).__name__}"]
        errors: list[str] = []
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in node:
                errors.append(f"{path}.{name}: required field missing")
        if schema.get("additionalProperties") is False:
            errors.extend(
                f"{path}.{key}: additional property not allowed"
                for key in node
                if key not in properties
            )
        for key, value in node.items():
            sub = properties.get(key)
            if isinstance(sub, dict):
                errors.extend(_validate_node(value, sub, f"{path}.{key}"))
        return errors
    if expected == "array":
        if not isinstance(node, list):
            return [f"{path}: expected array, got {type(node).__name__}"]
        items = schema.get("items")
        if not isinstance(items, dict):
            return []
        errors = []
        for i, item in enumerate(node):
            errors.extend(_validate_node(item, items, f"{path}[{i}]"))
        return errors
    if expected in _ALLOWED_TYPES and expected != "object":
        ok = {
            "string": isinstance(node, str),
            "number": isinstance(node, (int, float)) and not isinstance(node, bool),
            "integer": isinstance(node, int) and not isinstance(node, bool),
            "boolean": isinstance(node, bool),
        }.get(expected, True)
        if not ok:
            return [f"{path}: expected {expected}, got {type(node).__name__}"]
    enum = schema.get("enum")
    if isinstance(enum, list) and node not in enum:
        return [f"{path}: value {node!r} not in enum {enum}"]
    return []


def _decode_json(raw: object) -> dict[str, Any] | None:
    """Best-effort decode of a model reply (tolerates one fenced block)."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
        text = text.strip()
    try:
        decoded = json.loads(text)
    except (ValueError, TypeError):
        return None
    return decoded if isinstance(decoded, dict) else None


@dataclass(frozen=True)
class CanaryResult:
    """structured_output verdict for one model/backend."""

    model: str
    status: CanaryStatus
    detail: str
    raw_reply: str = ""


def run_canary(
    model: str,
    call_fn: Callable[[str, str], str],
) -> CanaryResult:
    """Adversarial canary: a constraint-respecting reply proves enforcement.

    The prompt asks for ``{"topic": "BANANA"}`` — a formatter that is live
    cannot honor it against the enum schema, so the reply must come back
    schema-valid. Transport failures degrade to ``unknown`` (never a pass).
    """
    try:
        raw = call_fn(_CANARY_PROMPT, model)
    except Exception as exc:
        return CanaryResult(model=model, status="unknown", detail=f"transport: {exc}")
    decoded = _decode_json(raw)
    if decoded is None:
        return CanaryResult(
            model=model,
            status="not_enforced",
            detail="reply is not a JSON object; format constraint ignored",
            raw_reply=str(raw)[:400],
        )
    errors = validate_against_schema(decoded, CANARY_SCHEMA)
    if errors:
        return CanaryResult(
            model=model,
            status="not_enforced",
            detail=f"reply violates the schema ({errors[0]}); constraint ignored",
            raw_reply=str(raw)[:400],
        )
    return CanaryResult(
        model=model,
        status="enforced",
        detail="formatter coerced the adversarial request into the schema",
        raw_reply=str(raw)[:400],
    )


@dataclass(frozen=True)
class StructuredOutcome:
    """One validated coding response (never coerced)."""

    model: str
    data: dict[str, Any] | None
    status: Literal["valid", "invalid_output", "transport_error"]
    attempts: int
    errors: list[str] = field(default_factory=list)

    @property
    def first_attempt_valid(self) -> bool:
        return self.status == "valid" and self.attempts == 1

    @property
    def repair_recovered(self) -> bool:
        return self.status == "valid" and self.attempts > 1


def structured_call(
    prompt: str,
    schema: dict[str, Any],
    model: str,
    call_fn: Callable[[str, str], str],
    max_retries: int = REPAIR_RETRIES_DEFAULT,
) -> StructuredOutcome:
    """Call, validate, retry with the appended error, then honest failure."""
    _reject_unsupported(schema)
    instruction = (
        f"{prompt}\nRespond with ONLY a JSON object matching this schema: "
        f"{json.dumps(schema, sort_keys=True)}"
    )
    current = instruction
    errors: list[str] = []
    for attempt in range(1, max_retries + 2):
        try:
            raw = call_fn(current, model)
        except Exception as exc:
            return StructuredOutcome(
                model=model,
                data=None,
                status="transport_error",
                attempts=attempt,
                errors=[str(exc)],
            )
        decoded = _decode_json(raw)
        if decoded is not None:
            errors = validate_against_schema(decoded, schema)
            if not errors:
                return StructuredOutcome(
                    model=model, data=decoded, status="valid", attempts=attempt
                )
        else:
            errors = [f"$: reply was not a JSON object: {str(raw)[:200]}"]
        current = (
            f"{instruction}\nYour previous reply was invalid: {'; '.join(errors)}. "
            "Respond again with ONLY corrected JSON matching the schema."
        )
    return StructuredOutcome(
        model=model,
        data=None,
        status="invalid_output",
        attempts=max_retries + 1,
        errors=errors,
    )


def enforcement_blocks(canary_status: CanaryStatus, require_enforcement: bool) -> bool:
    """U43(d): strict blocks coding/release on a not_enforced model."""
    return require_enforcement and canary_status == "not_enforced"
