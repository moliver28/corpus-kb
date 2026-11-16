"""Review-queue decision commands (accept / override).

Extracted from ``cli/__init__.py`` (size-ratchet F11): one operation
family over the one shared handler (``review_surface.execute_review``);
this module is the thin CLI adapter. The helpers come from
``cli_support`` and ``cli_store`` directly, so no import here touches
``corpus_kb.cli`` and the package import graph stays acyclic.
"""

from __future__ import annotations

import typer

from corpus_kb.cli.cli_store import wired_store
from corpus_kb.cli.cli_support import _run_async, _tenant


def _review_command(decision: str):
    def _cmd(
        assignment_id: str = typer.Argument(
            ..., help="Coding assignment UUID. corpus-kb coding assignments prints the list."
        ),
        reviewer: str = typer.Option(..., "--reviewer", help="Reviewer identity"),
        note: str = typer.Option(
            "", "--note", help="Decision note (link fixes describe the change)"
        ),
    ) -> int:
        """Record one reviewer decision and advance the projections."""
        import json

        async def _run() -> int:
            from uuid import UUID

            from corpus_kb.research.review_surface import execute_review

            async with wired_store() as wired:
                result = await execute_review(
                    wired.store,
                    _tenant(),
                    wired.conn_str,
                    UUID(assignment_id),
                    reviewer,
                    decision,
                    note,
                )
                print(json.dumps(result, indent=2, default=str))
                return 0

        return _run_async(_run())

    return _cmd


def attach_review_commands(review_app: typer.Typer) -> None:
    """Register the accept/override commands on the review sub-app."""
    review_app.command("accept", help="Confirm the model's assignment")(_review_command("accept"))
    review_app.command("override", help="Overrule the model's assignment")(
        _review_command("override")
    )
