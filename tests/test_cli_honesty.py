"""CLI honesty pins for the review-fix round additions: EOF-safe approval
denial, non-zero exit codes that actually reach the process, the
gray-zone halt listing the pending review items with their exact remedy
command, and the unattended --approve/--deny cycle flags. Offline only -
no DB, no Ollama.
"""

from __future__ import annotations

import asyncio
import inspect
import re
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


def test_root_transport_stdio_runs_the_mcp_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mcp-configs editors launch `corpus-kb --transport stdio`; the
    root callback must route to the MCP stdio server (never the HTTP
    stack), and report its taxonomy exit code."""
    from typer.testing import CliRunner

    calls: list[int] = []

    def _fake_server() -> int:
        calls.append(1)
        return 0

    monkeypatch.setattr("corpus_kb.mcp_server.run_stdio_server", _fake_server)

    result = CliRunner().invoke(cli.app, ["--transport", "stdio"])
    assert result.exit_code == 0, result.output
    assert calls == [1]


def test_root_transport_stdio_reports_config_failure_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stdio startup failure surfaces the taxonomy exit code (3)."""
    from typer.testing import CliRunner

    monkeypatch.setattr("corpus_kb.mcp_server.run_stdio_server", lambda: 3)

    result = CliRunner().invoke(cli.app, ["--transport", "stdio"])
    assert result.exit_code == 3


def test_cycle_help_lists_the_gate_exit_codes() -> None:
    """The --help epilog renders the cycle's exit-code contract from
    cycle_gates (the ONE table), so help cannot drift from the halts."""
    from typer.testing import CliRunner

    result = CliRunner().invoke(cli.app, ["research", "cycle", "--help"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Exit codes:" in out
    assert "0: run complete." in out
    assert f"{cycle_gates.EXIT_UNAVAILABLE}: embeddings unavailable" in out
    for gate, code in cycle_gates.GATE_EXIT_CODES.items():
        assert f"{code}: gate halt: {gate}." in out
    assert f"{cycle_gates.EXIT_APPROVAL_DENIED}: approval denied." in out
    assert f"{cycle_gates.EXIT_AWAITING_APPROVAL}: awaiting approval" in out


def test_root_transport_http_points_at_start() -> None:
    """The root --transport flag serves stdio only; HTTP has its own
    command, so anything else is an honest exit-2 pointer."""
    from typer.testing import CliRunner

    result = CliRunner().invoke(cli.app, ["--transport", "http"])
    assert result.exit_code == 2
    assert "corpus-kb start --transport http" in result.output


# ---------------------------------------------------------------------------
# Unattended approval flags (--approve / --deny): the headless cycle.
# The approval CALLBACK is the seam: the flags only pick which callback
# run_cycle awaits at each on-mode boundary, so nothing patches input()
# and a closed-stdin run cannot hang.
# ---------------------------------------------------------------------------


def _forbid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_stdin(prompt: str = "") -> str:
        raise AssertionError("stdin must not be read on an unattended run")

    monkeypatch.setattr("builtins.input", _no_stdin)


def _capture_cycle_coroutine(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
    """Stub cli_research._cycle_coroutine; record each call's positional args."""
    import corpus_kb.cli_research

    calls: list[tuple[object, ...]] = []

    def _factory(*args: object) -> object:
        calls.append(args)

        async def _run() -> int:
            return 0

        return _run()

    monkeypatch.setattr(corpus_kb.cli_research, "_cycle_coroutine", _factory)
    return calls


def test_cycle_approve_flag_runs_gates_without_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--approve wires the unattended yes callback; stdin stays closed."""
    from typer.testing import CliRunner

    _forbid_input(monkeypatch)
    calls = _capture_cycle_coroutine(monkeypatch)
    result = CliRunner().invoke(cli.app, ["research", "cycle", "--mode", "on", "--approve"])
    assert result.exit_code == 0, result.output
    assert calls, "the cycle coroutine must run"
    assert cli._cli_approve_yes in calls[0]


def test_cycle_deny_flag_denies_gates_without_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--deny wires the unattended no callback; stdin stays closed."""
    from typer.testing import CliRunner

    _forbid_input(monkeypatch)
    calls = _capture_cycle_coroutine(monkeypatch)
    result = CliRunner().invoke(cli.app, ["research", "cycle", "--mode", "on", "--deny"])
    assert result.exit_code == 0, result.output
    assert calls, "the cycle coroutine must run"
    assert cli._cli_approve_no in calls[0]


def test_cycle_approve_and_deny_conflict_is_exit_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both flags together are a usage error (exit 2) before any run."""
    from typer.testing import CliRunner

    calls = _capture_cycle_coroutine(monkeypatch)
    result = CliRunner().invoke(
        cli.app, ["research", "cycle", "--mode", "on", "--approve", "--deny"]
    )
    assert result.exit_code == 2
    # Rich styles the error box with ANSI escapes on CI terminals; strip
    # them before asserting on the plain text.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--approve" in plain and "--deny" in plain
    assert "cannot be used together" in plain
    assert calls == [], "a conflicting invocation must not start the cycle"


def test_cycle_default_flags_keep_the_interactive_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the flags the boundary stays the y/N prompt callback."""
    from typer.testing import CliRunner

    calls = _capture_cycle_coroutine(monkeypatch)
    result = CliRunner().invoke(cli.app, ["research", "cycle", "--mode", "on"])
    assert result.exit_code == 0, result.output
    assert calls, "the cycle coroutine must run"
    assert cli._cli_approve in calls[0]


def test_unattended_callbacks_are_async_and_never_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same awaitable contract as _cli_approve (F-2), fixed answers."""
    _forbid_input(monkeypatch)
    assert inspect.iscoroutinefunction(cli._cli_approve_yes)
    assert inspect.iscoroutinefunction(cli._cli_approve_no)
    assert asyncio.run(cli._cli_approve_yes("ingest")) is True
    assert asyncio.run(cli._cli_approve_no("ingest")) is False


async def test_unattended_approve_continues_at_the_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The --approve callback passes the real boundary: no stop, no events."""
    _forbid_input(monkeypatch)
    from corpus_kb.research import cycle_events as ev

    handler = _HaltHandler()
    recorder = ev.CycleRecorder(json_output=True)
    code = await cycle_render.approval(
        recorder,
        handler,
        (_Catch(), None, None, _Catch(), _Catch(), _Catch()),
        UUID(int=1),
        UUID(int=2),
        "ingest",
        cli._cli_approve_yes,
    )
    assert code is None
    assert handler.checkpoints == []
    assert handler.stopped == []


async def test_unattended_deny_stops_with_the_denied_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The --deny callback seals approval_denied and returns exit 20."""
    _forbid_input(monkeypatch)
    from corpus_kb.research import cycle_events as ev

    handler = _HaltHandler()
    recorder = ev.CycleRecorder(json_output=True)
    code = await cycle_render.approval(
        recorder,
        handler,
        (_Catch(), None, None, _Catch(), _Catch(), _Catch()),
        UUID(int=1),
        UUID(int=2),
        "ingest",
        cli._cli_approve_no,
    )
    assert code == cycle_gates.EXIT_APPROVAL_DENIED
    assert handler.checkpoints[-1]["kind"] == "approval_denied"
    assert handler.stopped == [UUID(int=2)]
