"""Chat doctor probe tests (Phase-2 Wave 7, work-order step 4).

Tmp config-dir fixtures cover: the dormant feature reports only INFO
rows, the absent login FAILs with the fix command, the token env
passes, the precedence-trap env WARNs, and the expiry row WARNs on a
fabricated credentials file — degrading to INFO when the file is
unreadable or carries no expiry.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from corpus_kb._setup.doctor_chat import chat_checks
from corpus_kb.config import get_default_config

_LOGIN_FIX_SNIPPET = "CLAUDE_CONFIG_DIR=~/.claude-corpus claude"


def _config(tmp_path: Path, **chat_overrides: Any) -> dict[str, Any]:
    config = dict(get_default_config())
    chat = dict(cast(dict[str, Any], config["chat"]))
    claude = dict(chat.get("claude") or {})
    claude["config_dir"] = str(tmp_path / "pin")
    chat["claude"] = claude
    chat.update(chat_overrides)
    config["chat"] = chat
    return config


def _statuses(config: dict[str, Any]) -> dict[str, str]:
    return {row.name: row.status for row in chat_checks(config)}


class TestDormantRows:
    def test_disabled_chat_reports_info_only(self, tmp_path: Path) -> None:
        statuses = _statuses(_config(tmp_path))
        assert statuses and set(statuses.values()) == {"info"}

    def test_dormant_rows_name_the_enable_flag(self, tmp_path: Path) -> None:
        rows = chat_checks(_config(tmp_path))
        assert all("chat.enabled" in row.fix for row in rows)


class TestEnabledRows:
    def test_absent_login_fails_with_fix(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=True)
        statuses = _statuses(config)
        assert statuses["chat login pin"] == "fail"
        login_row = next(row for row in chat_checks(config) if row.name == "chat login pin")
        assert _LOGIN_FIX_SNIPPET in login_row.fix

    def test_login_token_env_passes(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=True)
        claude = config["chat"]["claude"]
        claude["oauth_token"] = "tok"
        statuses = _statuses(config)
        assert statuses["chat login pin"] == "ok"

    def test_credentials_file_passes(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=True)
        pin = tmp_path / "pin"
        pin.mkdir()
        (pin / ".credentials.json").write_text("{}", encoding="utf-8")
        assert _statuses(config)["chat login pin"] == "ok"

    def test_trap_env_warns(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        config = _config(tmp_path, enabled=True)
        statuses = _statuses(config)
        assert statuses["chat auth precedence"] == "warn"
        row = next(row for row in chat_checks(config) if row.name == "chat auth precedence")
        assert "ANTHROPIC_API_KEY" in row.detail
        # The only escape named is ollama: anthropic-api refuses too.
        assert "chat.backend to ollama" in row.fix
        assert "anthropic-api" not in row.fix

    def test_clean_env_reports_ok(self, tmp_path: Path, monkeypatch: Any) -> None:
        for var in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        ):
            monkeypatch.delenv(var, raising=False)
        config = _config(tmp_path, enabled=True)
        assert _statuses(config)["chat auth precedence"] == "ok"

    def test_expiry_warn_within_three_days(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=True)
        pin = tmp_path / "pin"
        pin.mkdir()
        soon = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        (pin / ".credentials.json").write_text(json.dumps({"expiresAt": soon}), encoding="utf-8")
        statuses = _statuses(config)
        assert statuses["chat login expiry"] == "warn"

    def test_expiry_far_future_is_ok(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=True)
        pin = tmp_path / "pin"
        pin.mkdir()
        later = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        (pin / ".credentials.json").write_text(json.dumps({"expiresAt": later}), encoding="utf-8")
        assert _statuses(config)["chat login expiry"] == "ok"

    def test_unreadable_expiry_degrades_to_info(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=True)
        pin = tmp_path / "pin"
        pin.mkdir()
        (pin / ".credentials.json").write_text("not json", encoding="utf-8")
        assert _statuses(config)["chat login expiry"] == "info"

    def test_missing_expiry_file_is_info(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=True)
        assert _statuses(config)["chat login expiry"] == "info"

    def test_org_policy_row_says_where_refusals_show(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=True)
        row = next(row for row in chat_checks(config) if row.name == "chat org policy")
        assert "session start" in row.detail


class TestDoctorSnapshot:
    def test_snapshot_carries_the_chat_section(self) -> None:
        import asyncio

        from corpus_kb.tools.doctor_tools import doctor_snapshot

        snapshot = asyncio.run(doctor_snapshot({}))
        chat = snapshot["result"]["chat"]
        assert chat["enabled"] is False
        assert isinstance(chat["backend"], str)
        assert len(chat["checks"]) >= 6
