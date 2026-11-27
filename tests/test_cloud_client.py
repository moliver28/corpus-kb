"""Cloud coder client (TD-05): consent + restriction refusals, capability flags.

No live cloud call anywhere: the HTTP legs use an ``httpx.MockTransport``
fixture server (constraint 3; D-14). Refusal tests follow the
``test_backend_gates`` convention (spec gate 5).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from corpus_kb.coding.cloud_client import (
    CAPABILITIES,
    CLOUD_CONFIG_DEFAULTS,
    CloudConsentError,
    CloudRestrictedError,
    capability,
    check_consent,
    check_restricted,
    cloud_complete,
    egress_record,
)
from corpus_kb.errors import ConfigError

CONSENTED: dict[str, Any] = {
    **CLOUD_CONFIG_DEFAULTS,
    "consent": True,
    "api_key_env": "CORPUS_TEST_CLOUD_KEY",
}


def _recording_transport(captured: dict[str, Any], content: str = "ok") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def test_refusal_without_consent_names_the_key() -> None:
    with pytest.raises(CloudConsentError) as exc:
        check_consent(CLOUD_CONFIG_DEFAULTS)
    assert "coding.cloud.consent" in str(exc.value)
    assert exc.value.error_type == "config_error"


def test_restricted_refusal_names_the_table() -> None:
    with pytest.raises(CloudRestrictedError) as exc:
        check_restricted(True, "interviews_raw")
    assert "interviews_raw" in str(exc.value)
    assert exc.value.error_type == "write_refused"


def test_capability_table_matches_td05_point7() -> None:
    assert CAPABILITIES == {
        "logprobs": False,
        "seed": False,
        "n_samples": False,
        "temperature_zero": False,
        "json_schema": True,
    }
    assert capability("json_schema") is True
    with pytest.raises(ConfigError):
        capability("no_such_flag")


async def test_missing_api_key_env_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORPUS_TEST_CLOUD_KEY", raising=False)
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    with pytest.raises(ConfigError, match="CORPUS_TEST_CLOUD_KEY"):
        await cloud_complete("p", "claude-x", CONSENTED, transport=transport)


async def test_cloud_call_answers_content_usage_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("CORPUS_TEST_CLOUD_KEY", "k-test")
    result = await cloud_complete(
        "code this unit",
        "claude-haiku-4-5-20251001",
        CONSENTED,
        transport=_recording_transport(captured, content='{"decisions": []}'),
    )
    assert result["content"] == '{"decisions": []}'
    assert result["egress"]["provider"] == "anthropic-compat"
    assert result["egress"]["n_units"] == 1
    assert result["egress"]["restriction_check"] == "clear"
    assert captured["auth"].startswith("Bearer ")
    assert "k-test" not in json.dumps(result)  # the key never reaches the envelope


async def test_payload_never_carries_unsupported_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("CORPUS_TEST_CLOUD_KEY", "k-test")
    await cloud_complete(
        "p", "m", CONSENTED, temperature=0.0, transport=_recording_transport(captured)
    )
    payload = captured["payload"]
    assert "logprobs" not in payload
    assert "seed" not in payload
    assert "n" not in payload
    assert payload["temperature"] == 1.0  # floored: temperature_zero is false


async def test_restricted_unit_never_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORPUS_TEST_CLOUD_KEY", "k-test")
    with pytest.raises(CloudRestrictedError):
        await cloud_complete(
            "secret text",
            "m",
            CONSENTED,
            restricted=True,
            table="restricted_table",
            transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        )


def test_egress_record_carries_counts_never_text() -> None:
    record = egress_record("anthropic-compat", "m", 3, 1200, "clear")
    assert record == {
        "provider": "anthropic-compat",
        "model": "m",
        "n_units": 3,
        "n_bytes": 1200,
        "restriction_check": "clear",
    }
