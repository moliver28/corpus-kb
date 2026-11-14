"""Chat (Phase-2 Wave 7): the talk-to-your-corpus surface.

The package sits at the tools tier: ``api → chat → tools/handlers →
storage``. Two rungs ship wired (``claude-agent-sdk`` — the owner's
subscription, one owner on one machine; ``ollama`` — the ask machinery
multi-turn). ``anthropic-api`` is accepted in config but refuses at
session create until its wave lands ([D6]) — a validated value that
silently did nothing would break the honest-state rules.
"""

from __future__ import annotations

import asyncpg

from corpus_kb.chat.session import ChatManager, ChatSession
from corpus_kb.chat.settings import ChatSettings, read_chat_settings
from corpus_kb.errors import ConfigError, DependencyMissingError

__all__ = ["ChatSettings", "create_session", "get_manager", "read_chat_settings"]

_MANAGER: ChatManager | None = None


def enabled_rung(config: dict[str, object]) -> str:
    """The configured rung, refusing while chat is off or unwired.

    One truth for every surface: the routes call this BEFORE any pool
    work, so a disabled feature costs no connection attempt.
    """
    settings = read_chat_settings(config)
    if not settings.enabled:
        raise ConfigError(
            "Chat is disabled. Set chat.enabled to true in config.yaml. Then restart the server.",
            remediation="Set chat.enabled: true. Then restart the server.",
        )
    if settings.backend == "anthropic-api":
        raise DependencyMissingError(
            "The anthropic-api chat rung is not wired yet. Use claude-agent-sdk or ollama.",
            remediation="Set chat.backend to claude-agent-sdk or ollama.",
        )
    return settings.backend


async def create_session(
    config: dict[str, object],
    pg_pool: asyncpg.Pool,
    resume: str | None = None,
) -> ChatSession:
    """Build and register one session on the configured rung."""
    from corpus_kb.chat import claude_rung, ollama_rung

    settings = read_chat_settings(config)
    backend = enabled_rung(config)
    if backend == "claude-agent-sdk":
        session = await claude_rung.build_session(settings, config, pg_pool, resume)
    elif backend == "ollama":
        session = ollama_rung.build_session(settings, config, pg_pool, resume)
    else:
        raise ConfigError(
            f"chat.backend {settings.backend!r} is unknown. "
            "Use claude-agent-sdk, anthropic-api, or ollama.",
            remediation="Set chat.backend to a wired rung. Then restart the server.",
        )
    return await _manager(settings).create(session)


def _manager(settings: ChatSettings) -> ChatManager:
    """The process-wide manager, sized from the config once."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ChatManager(settings.max_sessions, settings.session_ttl_s)
    return _MANAGER


def get_manager() -> object | None:
    """The live manager, or None before the first session create."""
    return _MANAGER
