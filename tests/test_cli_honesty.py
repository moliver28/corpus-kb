"""CLI honesty pins for the review-fix round additions: EOF-safe approval
denial, non-zero exit codes that actually reach the process, and the
gray-zone halt listing the pending review items with their exact remedy
command. Offline only - no DB, no Ollama.
"""

from __future__ import annotations

import asyncio
from io import StringIO
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
import typer

from corpus_kb import cli
from corpus_kb.research import cycle_gates, cycle_render, guide_copy


def test_cli_approve_eof_denies_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _closed_stdin(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _closed_stdin)
    captured = StringIO()
    with patch("sys.stdout", captured):
        approved = asyncio.run(cli._cli_approve("ingest"))
    assert approved is False
    assert guide_copy.CYCLE_APPROVAL_NO_STDIN in captured.getvalue()


def test_run_async_raises_exit_for_nonzero_status() -> None:
    async def _failing() -> int:
        return 2

    with pytest.raises(typer.Exit) as excinfo:
        cli._run_async(_failing())
    assert excinfo.value.exit_code == 2


def test_run_async_returns_zero_cleanly() -> None:
    async def _ok() -> int:
        return 0

    assert cli._run_async(_ok()) == 0


class _HaltHandler:
    def __init__(self) -> None:
        self.checkpoints: list[dict[str, Any]] = []
        self.stopped: list[UUID] = []

    def checkpoint_coding_run(
        self, tenant_id: object, run_id: object, payload: dict[str, Any]
    ) -> None:
        self.checkpoints.append(payload)

    def stop_coding_run(self, tenant_id: object, run_id: object) -> None:
        self.stopped.append(cast_uuid(run_id))


def cast_uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


class _Catch:
    async def catch_up(self, *args: object, **kwargs: object) -> None:
        pass


def test_gray_zone_halt_lists_pending_review_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.research import cycle_events as ev

    assignment_id = "00000000-0000-0000-0000-00000000c0de"
    finding = cycle_gates.GateFinding(
        cycle_gates.GATE_GRAY_ZONE_ESCALATION,
        "1 review-queue item(s) still await a decision",
        {"pending_reviews": 1, "pending_ids": [assignment_id]},
    )
    handler = _HaltHandler()
    recorder = ev.CycleRecorder(json_output=True)
    captured = StringIO()
    with patch("sys.stdout", captured):
        code = asyncio.run(
            cycle_render.halt(
                handler,
                (_Catch(), None, None, _Catch(), _Catch(), _Catch()),
                recorder,
                False,
                object(),  # pool: unused on the non-guide path
                UUID(int=1),
                UUID(int=2),
                "deductive",
                [finding],
                finding,
            )
        )
    assert code == cycle_gates.GATE_EXIT_CODES["gray_zone_escalation"]
    out = captured.getvalue()
    assert guide_copy.CYCLE_PENDING_HEADER in out
    assert f"corpus-kb review accept {assignment_id}" in out


def test_root_transport_stdio_fails_honestly() -> None:
    """The mcp-configs invoke `corpus-kb --transport stdio`; until the MCP
    server exists the CLI must exit cleanly (2) with guidance instead of
    typer's "no such option" crash."""
    from typer.testing import CliRunner

    result = CliRunner().invoke(cli.app, ["--transport", "stdio"])
    assert result.exit_code == 2
    assert "does not yet speak MCP over stdio" in result.output
