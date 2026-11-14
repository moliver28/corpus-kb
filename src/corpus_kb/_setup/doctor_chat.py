"""Chat doctor probes (Phase-2 Wave 7, work-order step 4).

One probe set behind the chat section of ``doctor_snapshot`` — the CLI
doctor and ``GET /api/doctor`` read the same rows. Every row is INFO
while ``chat.enabled`` is false, so the doctor never nags about a
dormant feature. No probe raises; a probe that cannot run reports the
reason (the doctor contract). The file-backed login/binary/expiry
probes live in ``doctor_chat_login.py``; this module owns the
config-truth rows and the assembly.
"""

from __future__ import annotations

from pathlib import Path

from corpus_kb._setup.doctor_chat_login import binary_row, expiry_row, login_row
from corpus_kb._setup.doctor_research import (
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARN,
    ResearchCheck,
)

__all__ = ["chat_checks"]

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
        binary_row(dormant),
        login_row(pin, settings.claude.oauth_token, dormant),
        _precedence_row(env_refusal(), dormant),
        expiry_row(pin, dormant),
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
