"""Chat doctor login probes (Phase-2 Wave 7): pin, binary, expiry.

The file-backed half of the chat doctor: the CLI binary probe, the
work-account login row, and the credential-expiry row. Split from
``doctor_chat.py`` for the 250-line rule; ``chat_checks`` composes these
with the config-truth rows.

The probes read the pinned work account (``chat.claude.config_dir``)
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

__all__ = ["binary_row", "expiry_row", "login_row"]

_EXPIRY_SOON_DAYS = 3
_BINARY_TIMEOUT_S = 10
LOGIN_FIX = "Run: CLAUDE_CONFIG_DIR=~/.claude-corpus claude, then /login."


def binary_row(dormant: bool) -> ResearchCheck:
    """The Claude Code CLI binary probe."""
    if dormant:
        return ResearchCheck(
            name="chat CLI binary",
            status=STATUS_INFO,
            detail="Chat is off; the Claude Code CLI was not probed.",
            fix="",
            doc="docs/CHAT.md",
        )
    found, detail = _find_cli()
    return ResearchCheck(
        name="chat CLI binary",
        status=STATUS_OK if found else STATUS_FAIL,
        detail=detail,
        fix="" if found else "Install Claude Code natively; set chat.claude.cli_path.",
        doc="docs/CHAT.md",
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


def login_row(pin: Path, oauth_token: str, dormant: bool) -> ResearchCheck:
    """The work-account login row: FAIL with the fix when absent."""
    if dormant:
        return ResearchCheck(
            name="chat login pin",
            status=STATUS_INFO,
            detail=f"Chat is off; the work account at {pin} was not probed.",
            fix="",
            doc="docs/CHAT.md",
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
            doc="docs/CHAT.md",
        )
    return ResearchCheck(
        name="chat login pin",
        status=STATUS_FAIL,
        detail=f"No login in the pin ({pin}).",
        fix=LOGIN_FIX,
        doc="docs/CHAT.md",
    )


def expiry_row(pin: Path, dormant: bool) -> ResearchCheck:
    """The login-expiry row: WARN inside 3 days, INFO without a reading."""
    if dormant:
        return ResearchCheck(
            name="chat login expiry",
            status=STATUS_INFO,
            detail="Chat is off; the login expiry was not probed.",
            fix="",
            doc="docs/CHAT.md",
        )
    expires_at = _read_expiry(pin)
    if expires_at is None:
        return ResearchCheck(
            name="chat login expiry",
            status=STATUS_INFO,
            detail="No readable expiry in the pin. Run claude /status to see login state.",
            fix="",
            doc="docs/CHAT.md",
        )
    soon = datetime.now(UTC) + timedelta(days=_EXPIRY_SOON_DAYS)
    if expires_at < soon:
        return ResearchCheck(
            name="chat login expiry",
            status=STATUS_WARN,
            detail="Your login expires in 3 days or less. Run /login to renew.",
            fix=LOGIN_FIX,
            doc="docs/CHAT.md",
        )
    return ResearchCheck(
        name="chat login expiry",
        status=STATUS_OK,
        detail="The login is more than 3 days from expiry.",
        fix="",
        doc="docs/CHAT.md",
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
