"""The claude-agent-sdk rung: the owner's Claude subscription.

Session contract ([D3]-[D5]):
- env refusal at session start for every auth variable that outranks the
  subscription login (verified against the authentication precedence
  list, 2026-11-13) — never a silent billing-path switch, never env
  scrubbing;
- deny-by-default tooling: ``strict_mcp_config`` plus the one corpus
  server, the one pre-approved tool, the disallowed built-ins (including
  the auto-allowed read-only ones), a ``PreToolUse`` hook that gates
  every call (the only per-call gate; ``can_use_tool`` fires only when
  the CLI's rules say "ask"), and the ``can_use_tool`` backstop;
- ``setting_sources=[]`` — no filesystem settings load (None would load
  ALL sources; the SDK reference is explicit);
- the work-account pin via ``CLAUDE_CONFIG_DIR`` and a stable ``cwd``.

Every ``claude_agent_sdk`` import is lazy (``importlib`` at call time),
so base installs never import the SDK — and the import edge stays
invisible to static resolution, so pyright passes without the extra
(the ``ast_chunks`` contract for optional extras).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import asyncpg

from corpus_kb.chat.agent_tools import build_corpus_mcp_server
from corpus_kb.chat.claude_stream import turn_stream as _turn_stream
from corpus_kb.chat.session import ChatSession
from corpus_kb.chat.settings import ChatSettings, resolved_claude_config_dir, resolved_data_dir
from corpus_kb.errors import ConfigError, DependencyMissingError, InternalError

__all__ = ["PRECEDENCE_ENV_VARS", "build_session", "env_refusal", "list_transcripts"]


def _sdk_module() -> Any:
    """The SDK module, resolved at call time (the ``[chat]`` extra contract).

    ``importlib`` on purpose: the extra is optional and CI never installs
    it, so the import edge must stay invisible to static resolution (the
    ``ast_chunks`` contract). A missing extra answers the typed
    dependency error, never a bare ``ImportError``.
    """
    import importlib

    try:
        return importlib.import_module("claude_agent_sdk")
    except ImportError as exc:
        raise DependencyMissingError(
            "The chat extra is not installed. Install corpus-kb with the chat extra.",
            remediation='Install the extra: pip install -e ".[chat]". Then restart the server.',
        ) from exc


# [D4]: every variable that outranks the subscription login (rank 7).
# Verified live 2026-11-13: cloud-provider variables rank 1,
# ANTHROPIC_AUTH_TOKEN rank 2, ANTHROPIC_API_KEY rank 3. Any of the five
# silently re-routes billing away from the subscription.
PRECEDENCE_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

_CORPUS_TOOL_WIRE_NAME = "mcp__corpus__search"


def env_refusal(environ: dict[str, str] | None = None) -> str | None:
    """The [D4] refusal text when a higher-ranking auth variable is set.

    Session-start only, never load time: a stray variable must not break
    doctor or CLI reads for commands that do not chat. The text offers
    only ``ollama`` as the escape rung: ``anthropic-api`` refuses too
    until #428 wires it.
    """
    import os

    env = os.environ if environ is None else environ
    present = [name for name in PRECEDENCE_ENV_VARS if env.get(name)]
    if not present:
        return None
    return " ".join(
        f"{name} is set. It outranks the subscription login. Unset it "
        "for this server, or set chat.backend to ollama."
        for name in present
    )


async def list_transcripts(
    config: dict[str, object], limit: int | None = None, offset: int = 0
) -> list[dict[str, object]]:
    """The CLI's own session store, newest first ([D9]; no second store).

    The sync helper runs in a thread; it reads the transcripts the
    pinned ``cwd`` produced, so resume stays stable. A base install
    (no ``[chat]`` extra) answers a typed dependency error, never a
    500.
    """
    import asyncio

    sdk = _sdk_module()
    sdk_list_sessions = sdk.list_sessions

    directory = str(resolved_data_dir(config))

    def _read() -> list[object]:
        return list(sdk_list_sessions(directory=directory, limit=limit, offset=offset))

    rows = await asyncio.to_thread(_read)
    return [
        {
            "session_id": str(getattr(row, "session_id", "")),
            "summary": str(getattr(row, "summary", "")),
            "last_modified": getattr(row, "last_modified", 0),
            "first_prompt": getattr(row, "first_prompt", None),
        }
        for row in rows
    ]


async def build_session(
    settings: ChatSettings,
    config: dict[str, object],
    pg_pool: asyncpg.Pool,
    resume: str | None = None,
) -> ChatSession:
    """Create one claude-rung session: connect, then return it."""
    refusal = env_refusal()
    if refusal is not None:
        raise ConfigError(
            refusal,
            remediation="Unset the named variables. Then restart the server.",
        )
    sdk = _sdk_module()

    stderr_lines: list[str] = []
    session_id = resume or str(uuid4())
    options = _build_options(
        settings, config, pg_pool, stderr_lines.append, session_id=session_id, resume=resume
    )
    client = sdk.ClaudeSDKClient(options)
    try:
        await client.connect()
    except Exception as exc:
        raise _spawn_error(exc) from exc
    return ChatSession(
        session_id=session_id,
        backend="claude-agent-sdk",
        client=client,
        turn_stream=_turn_stream(stderr_lines),
        ttl_s=settings.session_ttl_s,
    )


def _spawn_error(exc: Exception) -> Exception:
    """Map the SDK spawn failures onto the taxonomy."""
    sdk = _sdk_module()

    if isinstance(exc, sdk.CLINotFoundError):
        return DependencyMissingError(
            "The Claude Code CLI is missing. Install Claude Code, then try again.",
            remediation=("Install Claude Code natively, or set chat.claude.cli_path. Then retry."),
        )
    if isinstance(exc, (sdk.CLIConnectionError, sdk.ProcessError)):
        return InternalError(str(exc))
    return InternalError(str(exc))


def _pinned_env(settings: ChatSettings) -> dict[str, str]:
    """The pin env, merged over the inherited env by the transport."""
    env = {"CLAUDE_CONFIG_DIR": str(resolved_claude_config_dir(settings))}
    if settings.claude.oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude.oauth_token
    return env


async def _deny_backstop(
    tool_name: str,
    tool_input: dict[str, object],
    _context: object | None = None,
) -> object:
    """The [D5] backstop: the corpus read or nothing.

    ``allowed_tools`` auto-approval never reaches this callback; it fires
    only for calls that would otherwise prompt — and it denies every
    name except the one corpus read. The ``PreToolUse`` hook
    (:func:`_tool_gate`) is the gate that also sees auto-allowed calls.
    """
    sdk = _sdk_module()

    if tool_name == _CORPUS_TOOL_WIRE_NAME:
        return sdk.PermissionResultAllow(updated_input=tool_input)
    return sdk.PermissionResultDeny(message="Chat may read the corpus and nothing else.")


async def _tool_gate(
    input_data: dict[str, object],
    _tool_use_id: str | None,
    _context: object,
) -> dict[str, object]:
    """The [D5] per-call gate: the corpus read or a hook deny.

    The SDK's own reference names this as the gate that fires for EVERY
    tool call regardless of permission rules — including the read-only
    built-ins the CLI auto-allows and never prompts for. A deny here
    blocks the call; ``{}`` (no opinion) leaves the call to the rules,
    where ``allowed_tools`` pre-approves the one corpus read.
    """
    tool_name = str(input_data.get("tool_name", "") or "")
    if tool_name == _CORPUS_TOOL_WIRE_NAME:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Chat may read the corpus and nothing else.",
        }
    }


def _build_options(
    settings: ChatSettings,
    config: dict[str, object],
    pg_pool: asyncpg.Pool,
    on_stderr: Callable[[str], None],
    session_id: str,
    resume: str | None,
) -> object:
    """``ClaudeAgentOptions`` exactly per [D5]; the SDK imports lazily."""
    sdk = _sdk_module()

    return sdk.ClaudeAgentOptions(
        cwd=str(resolved_data_dir(config)),
        env=_pinned_env(settings),
        cli_path=settings.claude.cli_path or None,
        model=settings.claude.model or None,
        max_turns=settings.claude.max_turns,
        allowed_tools=list(settings.claude.allowed_tools),
        disallowed_tools=list(settings.claude.disallowed_tools),
        strict_mcp_config=True,
        setting_sources=[],
        mcp_servers={"corpus": build_corpus_mcp_server(pg_pool, config)},
        can_use_tool=_deny_backstop,
        hooks={
            # The per-call gate ([D5]): fires for every call, including
            # the auto-allowed ones. on_failure="block" fails closed.
            "PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[_tool_gate], on_failure="block")]
        },
        include_partial_messages=True,
        stderr=on_stderr,
        resume=resume,
        # A fresh session carries the server-generated id, so the create
        # route knows the transcript id before the first turn.
        session_id=None if resume else session_id,
    )
