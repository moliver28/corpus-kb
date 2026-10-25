"""Research domain CLI commands (zero-drift Wave 3B split).

The ``research`` Typer sub-app. The transcript-ingest commands live in
cli_research_ingest, the notebook commands in cli_research_ask /
cli_research_views, the governance report in cli_research_report (the
250-line limit per file). Registration order here IS the help-output
order (ingest-transcript, ingest, demo, cycle, ask, evidence, uncoded,
overlap, report) — help text order is part of the CLI contract
(help-diff gate). Shared plumbing comes from cli_support; the heavy
research imports stay inside the functions.
"""

from __future__ import annotations

from typing import Any

import typer

from corpus_kb.cli.cli_research_ask import register as register_ask
from corpus_kb.cli.cli_research_ingest import register as register_ingest
from corpus_kb.cli.cli_research_report import register as register_report
from corpus_kb.cli.cli_research_views import register as register_views
from corpus_kb.cli.cli_support import (
    _cli_approve,
    _cli_approve_no,
    _cli_approve_yes,
    _pid,
    _run_async,
    _tenant,
    wired_pool,
)
from corpus_kb.research.cycle_gates import (
    EXIT_APPROVAL_DENIED,
    EXIT_AWAITING_APPROVAL,
    EXIT_UNAVAILABLE,
    GATE_EXIT_CODES,
)

research_app = typer.Typer(help="Research domain: transcript ingestion")

# The ingest commands register first (they are the first two in help).
register_ingest(research_app)


def _cycle_epilog() -> str:
    """Build the help epilog from cycle_gates (the ONE exit-code table).

    Help text is part of the CLI contract, so the codes render from the
    gate module's constants and cannot drift from the halts the command
    actually returns.
    """
    lines = [
        "Exit codes:",
        "  0: run complete.",
        f"  {EXIT_UNAVAILABLE}: embeddings unavailable; stage cannot run.",
    ]
    for gate, code in sorted(GATE_EXIT_CODES.items(), key=lambda item: item[1]):
        lines.append(f"  {code}: gate halt: {gate}.")
    lines.append(f"  {EXIT_APPROVAL_DENIED}: approval denied.")
    lines.append(f"  {EXIT_AWAITING_APPROVAL}: awaiting approval (mode on, no --approve).")
    return "\n".join(lines)


_CYCLE_EPILOG = _cycle_epilog()


def _approval_choice(approve: bool, deny: bool) -> object:
    """Pick the approval callback: an explicit flag wins over the prompt.

    The callback (cli_support._cli_approve or an unattended stub) is the
    ONE seam run_cycle awaits at each on-mode boundary; the flags only
    choose which callback runs. No stdin patching: a headless run with
    --approve never reaches input().
    """
    if approve and deny:
        raise typer.BadParameter("--approve and --deny cannot be used together.")
    if approve:
        return _cli_approve_yes
    if deny:
        return _cli_approve_no
    return _cli_approve


@research_app.command("demo")
def research_demo() -> int:
    """Narrated end-to-end pipeline on the bundled demo corpus (CLI only)."""

    from corpus_kb.config import load_config
    from corpus_kb.research.demo import run_demo

    # _run_async raises typer.Exit on any non-zero outcome (typer ignores a
    # plain int return), so a failed demo (embedder preflight, missing review
    # assignment) exits non-zero.
    return _run_async(run_demo(load_config()))


@research_app.command("cycle", epilog=_CYCLE_EPILOG)
def research_cycle(
    mode: str = typer.Option(
        "out", "--mode", help="in: exactly one stage | on: approve each stage | out: to the gates"
    ),
    project_id: str = typer.Option(None, "--project-id", help="Research project UUID"),
    drop_dir: str = typer.Option(None, "--dir", help="Transcript drop directory (ingest stage)"),
    question: str = typer.Option(None, "--question", help="Notebook question (final stage)"),
    codebook_version_id: str = typer.Option(
        None, "--codebook-version-id", help="Pin the codebook version (default: latest)"
    ),
    guide: bool = typer.Option(
        False, "--guide", help="Taught gates: narrate stages + halts with decision context"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Machine-readable stage/gate events (one JSON per line)"
    ),
    approve_all: bool = typer.Option(
        False,
        "--approve",
        help="Approve every gate without asking. Use only for unattended runs.",
    ),
    deny_all: bool = typer.Option(
        False,
        "--deny",
        help="Deny every gate without asking. Use only for unattended runs.",
    ),
    watch: str = typer.Option(
        None, "--watch", help="Foreground: re-run the cycle when transcripts land in this directory"
    ),
) -> int:
    """Research cycle (todo 20): full pipeline with human-critical halt gates.

    Halts print `AWAITING HUMAN: <gate>` plus the exact next command and a
    distinct per-gate exit code; `codebook_promotion` is hard-floored and
    can never be removed from the halt set. Resume by re-running this
    command after acting on the gate.
    """
    # _run_async raises typer.Exit on gate halts and denials (their exit
    # codes are the cycle's contract), so reaching here means exit 0.
    return _run_async(
        _cycle_coroutine(
            mode,
            project_id,
            drop_dir,
            question,
            codebook_version_id,
            guide,
            json_output,
            _approval_choice(approve_all, deny_all),
            watch,
        )
    )


def _cycle_coroutine(
    mode: str,
    project_id: str | None,
    drop_dir: str | None,
    question: str | None,
    codebook_version_id: str | None,
    guide: bool,
    json_output: bool,
    approve: object,
    watch: str | None,
):
    from typing import cast

    from corpus_kb.research.cycle import run_cycle
    from corpus_kb.research.cycle_state import watch_cycle

    async def _run() -> int:
        async with wired_pool() as wired:
            kwargs: dict[str, Any] = {
                "mode": mode,
                "project_id": _pid(project_id),
                "ingest_dir": drop_dir,
                "question": question,
                "codebook_version_id": _pid(codebook_version_id),
                "guide": guide,
                "json_output": json_output,
                "approve": cast("object", approve),
                "cfg": wired.cfg,
            }
            if watch:
                return await watch_cycle(wired.pool, _tenant(), watch, **kwargs)
            return await run_cycle(wired.pool, _tenant(), **kwargs)

    return _run()


# Notebook + report commands (own modules under the 250-line limit) attach
# here in the original help-output order: ask, evidence, uncoded, overlap,
# report.
register_ask(research_app)
register_views(research_app)
register_report(research_app)
