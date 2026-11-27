"""Cloud coder client (TD-05, R1): one OpenAI chat-completions client.

One client over httpx (the repo's declared HTTP library); no vendor SDK.
Default provider: Anthropic Claude through the compatibility base URL
(owner ruling "assume claude code", 2026-11-26). Consent is necessary,
not sufficient (C7): the call refuses without ``coding.cloud.consent``
and refuses a unit from a restricted table even with consent (AM-04);
the typed refusals name the key / the table. Capability flags (TD-05
point 7, platform.claude.com 2026-11-26): ``logprobs``, ``seed``, and
``n_samples`` false, ``temperature_zero`` false (temperature floors at
1), ``json_schema`` strict. A signal the endpoint cannot return is not
measured, never defaulted. The first cloud call writes one egress
record (no text) for the step ledger.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

from corpus_kb.errors import ConfigError, WriteRefusedError

__all__ = [
    "CAPABILITIES",
    "CLOUD_CONFIG_DEFAULTS",
    "CloudConsentError",
    "CloudRestrictedError",
    "capability",
    "check_consent",
    "check_restricted",
    "cloud_complete",
]

CONSENT_KEY = "coding.cloud.consent"

#: Shipped defaults (refine D-6, grounded 2026-11-27): dated Claude
#: snapshots through the compatibility layer, consent off. Disclosure: no
#: Claude model fits the TD-04 cloud-small band (<= $0.15 per 1M input
#: tokens); the three keys are ordinal ladder positions and the T2 pilot
#: re-snapshots both.
CLOUD_CONFIG_DEFAULTS: dict[str, Any] = {
    "base_url": "https://api.anthropic.com/v1/",
    "api_key_env": "ANTHROPIC_API_KEY",
    "consent": False,
    "models": {
        "small": "claude-haiku-4-5-20251001",
        "medium": "claude-sonnet-4-5-20250929",
        "large": "claude-opus-4-1-20250805",
    },
}

#: Capability flags for the default provider's chat-completions endpoint.
#: ``json_schema`` true (strict); the rest false. Data table plus one
#: generic check (TD-18 recount).
CAPABILITIES: dict[str, bool] = {
    "logprobs": False,
    "seed": False,
    "n_samples": False,
    "temperature_zero": False,
    "json_schema": True,
}


class CloudConsentError(ConfigError):
    """A cloud call was refused: ``coding.cloud.consent`` is not true."""


class CloudRestrictedError(WriteRefusedError):
    """A unit from a restricted table never reaches a cloud rung."""


def capability(flag: str) -> bool:
    """One endpoint capability flag; an unknown flag fails loud."""
    if flag not in CAPABILITIES:
        raise ConfigError(f"unknown cloud capability flag {flag!r}")
    return CAPABILITIES[flag]


def check_consent(cloud_cfg: Mapping[str, Any]) -> None:
    """Refuse a cloud call unless ``coding.cloud.consent`` is exactly true."""
    if cloud_cfg.get("consent") is not True:
        raise CloudConsentError(
            f"Cloud coding is refused: set {CONSENT_KEY} to true in config.yaml first."
        )


def check_restricted(restricted: bool, table: str) -> None:
    """Refuse when the unit's table is marked restricted; the refusal names it."""
    if restricted:
        raise CloudRestrictedError(
            f"Table {table!r} is marked restricted; its units never reach a cloud rung."
        )


def resolve_api_key(cloud_cfg: Mapping[str, Any]) -> str:
    """The key from the environment variable ``coding.cloud.api_key_env``.

    The key never reaches a log, a trace row, or an envelope (C7).
    """
    env_name = str(cloud_cfg.get("api_key_env") or "")
    if not env_name:
        raise ConfigError("coding.cloud.api_key_env must name an environment variable")
    key = os.environ.get(env_name)
    if not key:
        raise ConfigError(f"Environment variable {env_name} is not set; no cloud key found.")
    return key


def egress_record(
    provider: str, model: str, n_units: int, n_bytes: int, restriction: str
) -> dict[str, Any]:
    """One egress record for the step ledger: counts only, never text (C7)."""
    return {
        "provider": provider,
        "model": model,
        "n_units": n_units,
        "n_bytes": n_bytes,
        "restriction_check": restriction,
    }


async def cloud_complete(
    prompt: str,
    model: str,
    cloud_cfg: Mapping[str, Any],
    *,
    system: str | None = None,
    temperature: float = 1.0,
    json_schema: dict[str, Any] | None = None,
    restricted: bool = False,
    table: str = "",
    n_units: int = 1,
    transport: httpx.AsyncTransport | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """One chat-completions call behind consent + restriction guards.

    The payload never carries ``logprobs``, ``seed``, or ``n`` greater
    than one (capability flags), and temperature is floored at 1 because
    ``temperature_zero`` is false on this endpoint. ``restricted`` plus
    ``table`` carry the de-identification status check result. The answer
    carries the reply text, the usage block, and the egress record.
    """
    check_consent(cloud_cfg)
    check_restricted(restricted, table)
    api_key = resolve_api_key(cloud_cfg)
    base_url = str(cloud_cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ConfigError("coding.cloud.base_url must name the compatibility endpoint")
    payload: dict[str, Any] = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else [])
        + [{"role": "user", "content": prompt}],
        "temperature": max(temperature, 1.0),
    }
    if json_schema is not None:
        payload["response_format"] = {"type": "json_schema", "json_schema": json_schema}
    n_bytes = len(str(payload).encode("utf-8"))
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(transport=transport, timeout=timeout_seconds) as client:
        response = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    record = egress_record(
        "anthropic-compat", model, n_units, n_bytes, "refused" if restricted else "clear"
    )
    content = ""
    choices = body.get("choices") if isinstance(body, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = str(message.get("content") or "") if isinstance(message, dict) else ""
    return {
        "content": content,
        "usage": body.get("usage") if isinstance(body, dict) else None,
        "egress": record,
    }
