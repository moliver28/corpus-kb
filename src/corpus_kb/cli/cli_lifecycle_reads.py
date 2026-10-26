"""Lifecycle read-list CLI commands (UI SPA program Task 5b, spec B2).

Read-only list commands for the lifecycle screens' data, attached to their
home groups by the home modules (registration order is help order):

  * ``corpus-kb research projects``     — projects_list
  * ``corpus-kb codebook list``         — codebooks_list
  * ``corpus-kb codebook proposals``    — codebook_proposals
  * ``corpus-kb coding assignments``    — coding_assignments

Each prints the tool's house envelope as JSON and exits 0 on success,
2 when the tables are missing (unavailable), 1 on any other failure —
the same codes the inductive pass uses. The ONE tool functions in
``tools/lifecycle_read_tools.py`` are the handler; this module adds
argument parsing and printing only.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from corpus_kb.cli.cli_support import DEFAULT_TENANT, _run_async, _tenant, wired_pool

__all__ = ["register_coding", "register_research"]

_EXIT_ERROR = 1
_EXIT_UNAVAILABLE = 2


def _print_envelope(envelope: dict[str, Any]) -> int:
    """Print one tool envelope; map its status to the honest exit code."""
    print(json.dumps(envelope, indent=2, default=str))
    status = str(envelope.get("status", ""))
    if status == "unavailable":
        return _EXIT_UNAVAILABLE
    return 0 if status == "success" else _EXIT_ERROR


def _read_coroutine(fn: Any, kwargs: dict[str, Any]):
    """One shared read runner: wired pool, default tenant, JSON out."""

    async def _run() -> int:
        async with wired_pool() as wired:
            kwargs.setdefault("tenant_id", _tenant() or DEFAULT_TENANT)
            return _print_envelope(await fn(wired.pool, **kwargs))

    return _run()


def register_research(research_app: typer.Typer) -> None:
    """Attach ``research projects`` to the research group."""

    @research_app.command("projects")
    def projects(
        limit: int = typer.Option(100, "--limit", help="Page size (1-500)"),
    ) -> int:
        """List research projects, newest first (read-only)."""
        from corpus_kb.tools.lifecycle_read_tools import projects_list

        return _run_async(_read_coroutine(projects_list, {"limit": limit}))


def register_coding(coding_app: typer.Typer, codebook_app: typer.Typer) -> None:
    """Attach the codebook + coding read commands to their groups."""

    @codebook_app.command("list")
    def codebook_list(
        limit: int = typer.Option(100, "--limit", help="Page size (1-500)"),
    ) -> int:
        """List codebook versions with hashes and dates (read-only)."""
        from corpus_kb.tools.lifecycle_read_tools import codebooks_list

        return _run_async(_read_coroutine(codebooks_list, {"limit": limit}))

    @codebook_app.command("proposals")
    def codebook_proposals(
        status: str = typer.Option(
            "proposed", "--status", help="proposed|promoted|rejected|duplicate_blocked"
        ),
        limit: int = typer.Option(100, "--limit", help="Page size (1-500)"),
    ) -> int:
        """List proposed codes for the promote gate (read-only)."""
        from corpus_kb.tools.lifecycle_read_tools import codebook_proposals

        return _run_async(_read_coroutine(codebook_proposals, {"status": status, "limit": limit}))

    @coding_app.command("assignments")
    def coding_assignments(
        review_status: str = typer.Option(
            None, "--review-status", help="auto|review|confirmed|overridden"
        ),
        code: str = typer.Option(None, "--code", help="One code id"),
        limit: int = typer.Option(100, "--limit", help="Page size (1-500)"),
        offset: int = typer.Option(0, "--offset", help="Rows to skip"),
    ) -> int:
        """List coding assignments for the review queue (read-only)."""
        from corpus_kb.tools.lifecycle_read_tools import coding_assignments

        return _run_async(
            _read_coroutine(
                coding_assignments,
                {
                    "review_status": review_status,
                    "code": code,
                    "limit": limit,
                    "offset": offset,
                },
            )
        )
