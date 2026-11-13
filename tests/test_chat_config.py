"""Chat config gate tests (Phase-2 Wave 7, work-order step 1).

``chat_backend_errors`` refuses, at load time, any chat config that
breaks the one-owner subscription rung: an unknown backend, the
claude-agent-sdk rung off loopback or without a single owner, or
``chat.enabled`` with no backend. Env checks are NOT here — they run at
session start so a stray variable never breaks doctor or CLI reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb.config import chat_backend_errors, get_default_config, load_config


def _config(**chat_overrides: object) -> dict[str, object]:
    """One valid default config with a mutated ``chat`` block."""
    config = get_default_config()
    chat_raw = config.get("chat")
    chat = dict(chat_raw) if isinstance(chat_raw, dict) else {}
    chat.update(chat_overrides)
    config["chat"] = chat
    return config


def _config_host(host: str) -> dict[str, object]:
    config = get_default_config()
    server_raw = config.get("server")
    server = dict(server_raw) if isinstance(server_raw, dict) else {}
    server["host"] = host
    config["server"] = server
    return config


class TestChatBackendErrors:
    def test_defaults_pass(self) -> None:
        assert chat_backend_errors(get_default_config()) == []

    def test_loopback_hosts_pass(self) -> None:
        for host in ("localhost", "127.0.0.1", "::1"):
            assert chat_backend_errors(_config_host(host)) == []

    def test_non_loopback_host_refused(self) -> None:
        errors = chat_backend_errors(_config_host("0.0.0.0"))
        assert len(errors) == 1
        assert "ollama" in errors[0]
        assert "loopback" in errors[0]

    def test_single_owner_false_refused(self) -> None:
        errors = chat_backend_errors(_config(single_owner=False))
        assert len(errors) == 1
        assert "single_owner" in errors[0]

    def test_unknown_backend_refused(self) -> None:
        errors = chat_backend_errors(_config(backend="gpt"))
        assert len(errors) == 1
        assert "unknown" in errors[0]

    def test_enabled_with_empty_backend_refused(self) -> None:
        errors = chat_backend_errors(_config(enabled=True, backend=""))
        assert len(errors) == 1
        assert "empty" in errors[0]

    def test_dormant_with_empty_backend_passes(self) -> None:
        assert chat_backend_errors(_config(enabled=False, backend="")) == []


class TestLoadTimeEnforcement:
    def test_load_raises_on_non_loopback(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "chat:\n  enabled: true\n  backend: claude-agent-sdk\nserver:\n  host: 0.0.0.0\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="loopback"):
            load_config(path=str(cfg_file))

    def test_loaded_defaults_are_dormant_and_clean(self) -> None:
        config = load_config(path="/nonexistent/config.yaml")
        chat = config["chat"]
        assert isinstance(chat, dict)
        assert chat["enabled"] is False
        assert chat["backend"] == "claude-agent-sdk"
        assert chat_backend_errors(config) == []


class TestEnvOverride:
    def test_oauth_token_env_lands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORPUS_KB_CHAT_CLAUDE_OAUTH_TOKEN", "tok-123")
        config = load_config(path="/nonexistent/config.yaml")
        claude = config["chat"]
        assert isinstance(claude, dict)
        claude_block = claude["claude"]
        assert isinstance(claude_block, dict)
        assert claude_block["oauth_token"] == "tok-123"

    def test_oauth_token_absent_stays_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CORPUS_KB_CHAT_CLAUDE_OAUTH_TOKEN", raising=False)
        config = load_config(path="/nonexistent/config.yaml")
        claude = config["chat"]
        assert isinstance(claude, dict)
        claude_block = claude["claude"]
        assert isinstance(claude_block, dict)
        assert claude_block["oauth_token"] == ""
