"""Tests for the extracted browser auto-open helpers.

The block moved out of ``server_wiring`` (size-ratchet F11). These tests
pin the guard, the schedule branches, and the open call — the browser
itself is monkeypatched, so nothing opens on a headless runner.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from corpus_kb.ui.browser_open import (
    browser_open_enabled,
    open_browser_when_up,
    schedule_browser_open,
)


def test_guard_blocks_only_the_explicit_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CORPUS_KB_UI_OPEN=0`` keeps the tab closed; anything else opens."""
    monkeypatch.setenv("CORPUS_KB_UI_OPEN", "0")
    assert browser_open_enabled() is False
    monkeypatch.setenv("CORPUS_KB_UI_OPEN", "1")
    assert browser_open_enabled() is True
    monkeypatch.delenv("CORPUS_KB_UI_OPEN")
    assert browser_open_enabled() is True


def test_schedule_skips_when_the_spa_did_not_mount() -> None:
    """No SPA mount, no tab: the task is None."""
    assert schedule_browser_open({"ui_mounted": False}, "localhost", 8010) is None


def test_schedule_skips_when_the_guard_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SPA mounted but the env guard says keep the tab closed."""
    monkeypatch.setenv("CORPUS_KB_UI_OPEN", "0")
    services: dict[str, Any] = {"ui_mounted": True}
    assert schedule_browser_open(services, "localhost", 8010) is None


async def test_schedule_returns_a_task_and_open_fires_the_root_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mounted + allowed: the delayed open hits the server root URL."""
    opened: list[str] = []
    monkeypatch.setattr(
        "webbrowser.open",
        lambda url: opened.append(url),
    )
    services: dict[str, Any] = {"ui_mounted": True}
    task = schedule_browser_open(services, "127.0.0.1", 8010)
    assert task is not None
    await asyncio.wait_for(task, timeout=5)
    assert opened == ["http://127.0.0.1:8010"]


def test_open_browser_waits_then_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    """The open call goes to http://host:port after the delay."""
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    asyncio.run(open_browser_when_up("localhost", 9999, delay=0.01))
    assert opened == ["http://localhost:9999"]
