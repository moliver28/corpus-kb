"""Chat doctor probes (Phase-2 Wave 7, work-order step 4).

One probe set behind the chat section of ``doctor_snapshot`` — the CLI
doctor and ``GET /api/doctor`` read the same rows. Every row is INFO
while ``chat.enabled`` is false, so the doctor never nags about a
dormant feature. No probe raises; a probe that cannot run reports the
reason (the doctor contract).

The login probes read the pinned work account (``chat.claude.config_dir``)
only. Contents of credential files are NEVER printed — presence and
expiry shape only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from corpus_kb._setup.doctor_research import (
    STATUS_FAIL,
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARN,
    ResearchCheck,
)

__all__ = ["chat_checks"]

_EXPIRY_SOON_DAYS = 3
_BINARY_TIMEOUT_S = 10
_LOGIN_FIX = "Run: CLAUDE_CONFIG_DIR=~/.claude-corpus claude, then /login."
_DOC_CHAT = "docs/CHAT.md"


def chat_checks(config: dict[str, object]) -> list[ResearchCheck]:
    """The chat section rows, in the work-order's fixed order."""
    from corpus_kb.chat.claude_rung import env_refusal
    from corpus_kb.chat.settings import read_chat_settings, resolved_claude_config_dir

    settings = read_chat_settings(config)
    dormant = not settings.enabled
    pin: Path = resolved_claude_config_dir(settings)

    rows = [
        _rung_row(settings, dormant),
        _binary_row(dormant),
        _login_row(pin, settings.claude.oauth_token, dormant),
        _precedence_row(env_refusal(), dormant),
        _expiry_row(pin, dormant),
        _org_policy_row(dormant),
    ]
    if dormant:
        return [
            ResearchCheck(
                name=row.name,
                status=STATUS_INFO,
                detail=row.detail,
                fix="Set chat.enabled: true in config.yaml. Then restart the server.",
                doc=_DOC_CHAT,
            )
            for row in rows
        ]
    return rows


def _rung_row(settings: object, dormant: bool) -> ResearchCheck:
    backend = getattr(settings, "backend", "")
    if dormant:
        return ResearchCheck(
            name="chat rung",
            status=STATUS_INFO,
            detail="Chat is off. The configured rung is dormant until you enable it.",
            fix="Set chat.enabled: true in config.yaml. Then restart the server.",
            doc=_DOC_CHAT,
        )
    return ResearchCheck(
        name="chat rung",
        status=STATUS_INFO,
        detail=f"Chat is on. The rung is {backend}.",
        fix="",
        doc=_DOC_CHAT,
    )


def _binary_row(dormant: bool) -> ResearchCheck:
    if dormant:
        return ResearchCheck(
            name="chat CLI binary",
            status=STATUS_INFO,
            detail="Chat is off; the Claude Code CLI was not probed.",
            fix="",
            doc=_DOC_CHAT,
        )
    found, detail = _find_cli()
    return ResearchCheck(
        name="chat CLI binary",
        status=STATUS_OK if found else STATUS_FAIL,
        detail=detail,
        fix="" if found else "Install Claude Code natively; set chat.claude.cli_path.",
        doc=_DOC_CHAT,
    )


def _find_cli() -> tuple[bool, str]:
    """The Claude Code CLI on PATH, else the SDK's bundled binary."""
    claude = shutil.which("claude")
    if claude:
        try:
            probe = subprocess.run(
                [claude, "--version"],
                capture_output=True,
                text=True,
                timeout=_BINARY_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False, "claude is on PATH but did not answer within 10 seconds."
        if probe.returncode == 0:
            return True, f"claude on PATH answered: {probe.stdout.strip()[:80]}"
        return False, "claude is on PATH but --version failed."
    try:
        import claude_agent_sdk  # noqa: F401  # the SDK extra
    except ImportError:
        return False, "No claude on PATH, and the [chat] extra is not installed."
    if _arm64_windows():
        return (
            False,
            "No claude on PATH. The SDK has no ARM64-Windows binary; install Claude Code natively.",
        )
    return False, "No claude on PATH, and the SDK ships no bundled binary for this platform."


def _arm64_windows() -> bool:
    return sys.platform == "win32" and sys.maxsize > 2**32 and _machine_is_arm64()


def _machine_is_arm64() -> bool:
    import platform

    return platform.machine().upper() in {"ARM64", "AARCH64"}


def _login_row(pin: Path, oauth_token: str, dormant: bool) -> ResearchCheck:
    if dormant:
        return ResearchCheck(
            name="chat login pin",
            status=STATUS_INFO,
            detail=f"Chat is off; the work account at {pin} was not probed.",
            fix="",
            doc=_DOC_CHAT,
        )
    credentials = pin / ".credentials.json"
    if credentials.exists() or oauth_token:
        source = (
            "the pinned directory" if credentials.exists() else "CORPUS_KB_CHAT_CLAUDE_OAUTH_TOKEN"
        )
        return ResearchCheck(
            name="chat login pin",
            status=STATUS_OK,
            detail=f"A login is present in {source}.",
            fix="",
            doc=_DOC_CHAT,
        )
    return ResearchCheck(
        name="chat login pin",
        status=STATUS_FAIL,
        detail=f"No login in the pin ({pin}).",
        fix=_LOGIN_FIX,
        doc=_DOC_CHAT,
    )


def _precedence_row(refusal: str | None, dormant: bool) -> ResearchCheck:
    if dormant or refusal is None:
        return ResearchCheck(
            name="chat auth precedence",
            status=STATUS_INFO if dormant else STATUS_OK,
            detail=(
                "Chat is off; the auth variables were not probed."
                if dormant
                else "No auth variable outranks the subscription login."
            ),
            fix="",
            doc=_DOC_CHAT,
        )
    return ResearchCheck(
        name="chat auth precedence",
        status=STATUS_WARN,
        detail=refusal,
        fix="Unset the named variables for this server,"
        " or set chat.backend to anthropic-api or ollama.",
        doc=_DOC_CHAT,
    )


def _expiry_row(pin: Path, dormant: bool) -> ResearchCheck:
    if dormant:
        return ResearchCheck(
            name="chat login expiry",
            status=STATUS_INFO,
            detail="Chat is off; the login expiry was not probed.",
            fix="",
            doc=_DOC_CHAT,
        )
    expires_at = _read_expiry(pin)
    if expires_at is None:
        return ResearchCheck(
            name="chat login expiry",
            status=STATUS_INFO,
            detail="No readable expiry in the pin. Run claude /status to see login state.",
            fix="",
            doc=_DOC_CHAT,
        )
    soon = datetime.now(UTC) + timedelta(days=_EXPIRY_SOON_DAYS)
    if expires_at < soon:
        return ResearchCheck(
            name="chat login expiry",
            status=STATUS_WARN,
            detail="Your login expires in 3 days or less. Run /login to renew.",
            fix="Run: CLAUDE_CONFIG_DIR=~/.claude-corpus claude, then /login.",
            doc=_DOC_CHAT,
        )
    return ResearchCheck(
        name="chat login expiry",
        status=STATUS_OK,
        detail="The login is more than 3 days from expiry.",
        fix="",
        doc=_DOC_CHAT,
    )


def _read_expiry(pin: Path) -> datetime | None:
    """The ``expiresAt`` field, or None when absent or unreadable.

    Parsing an undocumented internal is NOT load-bearing: absence
    degrades to the INFO row, never a false FAIL.
    """
    credentials = pin / ".credentials.json"
    try:
        data = json.loads(credentials.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("expiresAt")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _org_policy_row(dormant: bool) -> ResearchCheck:
    detail = (
        "Chat is off."
        if dormant
        else "Org-policy refusals show at session start as warnings in chat."
        " The doctor cannot run /status."
    )
    return ResearchCheck(
        name="chat org policy",
        status=STATUS_INFO,
        detail=detail,
        fix="",
        doc=_DOC_CHAT,
    )
