"""The ``corpus-kb events`` command (issue #350).

One top-level read command over the event-store health op: event counts
by topic, the latest notification id, each projection checkpoint, the
dead-letter count with the newest five entries, and the last five
ingest runs. Thin adapter over ``tools.events_tools.events_summary`` —
the same function the MCP tool, the socket arm, and the GET route serve.

HONEST EXIT CODES: ``0`` the command answered, ``2`` the tables are
missing (unavailable), ``1`` any other failure.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from corpus_kb.cli.cli_store import wired_store
from corpus_kb.cli.cli_support import DEFAULT_TENANT, _run_async, _tenant

__all__ = ["register"]

_EXIT_ERROR = 1
_EXIT_UNAVAILABLE = 2


def register(app: typer.Typer) -> None:
    """Attach the events command to the main Typer app."""

    @app.command("events")
    def events(
        json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
    ) -> int:
        """Event-store health: topics, checkpoints, dead letters, runs."""
        from corpus_kb.tools.events_tools import events_summary

        async def _run() -> int:
            envelope: dict[str, Any]
            async with wired_store() as wired:
                envelope = await events_summary(
                    wired.store, tenant_id=str(_tenant() or DEFAULT_TENANT)
                )
            print(
                json.dumps(envelope, indent=2, default=str)
                if json_output
                else json.dumps(_summary(envelope), indent=2, default=str)
            )
            if envelope.get("status") == "unavailable":
                return _EXIT_UNAVAILABLE
            return 0 if envelope.get("status") == "success" else _EXIT_ERROR

        return _run_async(_run())


def _summary(envelope: dict[str, Any]) -> dict[str, Any]:
    """The short human view: headline counts plus the topic table."""
    dead = envelope.get("dead_letters") or {}
    return {
        "status": envelope.get("status"),
        "events_store": envelope.get("events_store"),
        "latest_notification_id": envelope.get("latest_notification_id"),
        "events_by_topic": envelope.get("events_by_topic") or {},
        "dead_letter_count": dead.get("count", 0),
        "checkpoint_count": len(envelope.get("checkpoints") or []),
        "recent_run_count": len(envelope.get("recent_runs") or []),
    }
