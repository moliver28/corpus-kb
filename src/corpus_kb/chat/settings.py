"""Typed readers for the ``chat:`` config section.

House rule: internal models are dataclasses, never Pydantic. The leaves
mirror ``get_default_config()``'s ``chat`` block (config.py); the
lockstep test keeps the two in agreement, so a missing key here is a
config-shape bug, not a reader fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from corpus_kb.config import get_default_config

__all__ = [
    "ChatSettings",
    "ClaudeRungSettings",
    "OllamaRungSettings",
    "read_chat_settings",
    "resolved_claude_config_dir",
    "resolved_data_dir",
]


@dataclass
class ClaudeRungSettings:
    """The claude-agent-sdk rung's leaves."""

    config_dir: str
    model: str
    max_turns: int
    allowed_tools: list[str]
    disallowed_tools: list[str]
    oauth_token: str


@dataclass
class OllamaRungSettings:
    """The ollama rung's leaves."""

    history_turns: int


@dataclass
class ChatSettings:
    """The whole ``chat:`` block, read once per session create."""

    enabled: bool
    backend: str
    single_owner: bool
    max_sessions: int
    session_ttl_s: int
    claude: ClaudeRungSettings
    ollama: OllamaRungSettings


def _section(config: dict[str, object], name: str) -> dict[str, object]:
    raw = config.get(name)
    return cast(dict[str, object], raw) if isinstance(raw, dict) else {}


def _overlay(defaults: dict[str, object], user: dict[str, object]) -> dict[str, object]:
    """Copy defaults, then overlay user leaves one level deep.

    A mapping leaf merges; every other user value (including an explicit
    empty list) replaces the default outright.
    """
    merged = dict(defaults)
    for key, value in user.items():
        base = merged.get(key)
        if isinstance(base, dict) and isinstance(value, dict):
            merged[key] = _overlay(base, value)
        else:
            merged[key] = value
    return merged


def _str(block: dict[str, object], key: str, default: str = "") -> str:
    value = block.get(key)
    return str(value) if isinstance(value, str) else default


def _int(block: dict[str, object], key: str, default: int) -> int:
    value = block.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _str_list(block: dict[str, object], key: str) -> list[str]:
    value = block.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def read_chat_settings(config: dict[str, object]) -> ChatSettings:
    """Read the ``chat:`` block into typed settings.

    Hand-built or partial config trees read as the code defaults (the
    same rule ``load_config`` applies): the user block overlays the
    default block leaf by leaf, so an explicit empty list stays empty.
    """
    defaults = _section(get_default_config(), "chat")
    chat = _overlay(defaults, _section(config, "chat"))
    claude_raw = _section(chat, "claude")
    ollama_raw = _section(chat, "ollama")
    enabled = chat.get("enabled")
    single_owner = chat.get("single_owner")
    return ChatSettings(
        enabled=enabled is True,
        backend=_str(chat, "backend"),
        single_owner=single_owner is not False,
        max_sessions=_int(chat, "max_sessions", 1),
        session_ttl_s=_int(chat, "session_ttl_s", 1800),
        claude=ClaudeRungSettings(
            config_dir=_str(claude_raw, "config_dir", "~/.claude-corpus"),
            model=_str(claude_raw, "model"),
            max_turns=_int(claude_raw, "max_turns", 20),
            allowed_tools=_str_list(claude_raw, "allowed_tools"),
            disallowed_tools=_str_list(claude_raw, "disallowed_tools"),
            oauth_token=_str(claude_raw, "oauth_token"),
        ),
        ollama=OllamaRungSettings(
            history_turns=_int(ollama_raw, "history_turns", 6),
        ),
    )


def resolved_claude_config_dir(settings: ChatSettings) -> Path:
    """The pinned work-account directory, expanded at read time."""
    return Path(settings.claude.config_dir).expanduser()


def resolved_data_dir(config: dict[str, object]) -> Path:
    """The agent's working directory: ``installer.data_dir``, expanded.

    Pinning ``cwd`` here keeps the CLI transcripts (and ``list_sessions``)
    stable for this server, whatever directory the process started in.
    """
    installer = _section(config, "installer")
    raw = _str(installer, "data_dir", "~/.corpus-kb")
    return Path(raw).expanduser()
