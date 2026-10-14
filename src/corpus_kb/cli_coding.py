"""Coding + codebook CLI commands (zero-drift Wave 3B split).

Moved byte-identical from cli.py (the 250-line limit):

  * ``corpus-kb coding run``        — deductive coding run (v5 §8).
  * ``corpus-kb coding inductive``  — inductive pass (v5 §9).
  * ``corpus-kb codebook promote``  — human-gated code promotion.

The Typer sub-apps are created here and attached by cli.py; the command
registration order inside each sub-app is unchanged (run, inductive /
promote). Shared plumbing (``_run_async``, ``wired_pool``, the tenant id)
comes from cli_support; the heavy research imports stay inside the
functions.
"""

from __future__ import annotations

import typer

from corpus_kb.cli_support import DEFAULT_TENANT, _run_async, wired_pool

coding_app = typer.Typer(help="Coding subsystem: deductive coding runs")
codebook_app = typer.Typer(help="Codebook governance: human-gated promotion")


@coding_app.command("run")
def coding_run(
    codebook_version_id: str = typer.Option(
        ..., "--codebook-version-id", help="Codebook version UUID to code against"
    ),
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one research project"),
    alpha: float = typer.Option(
        0.1, "--alpha", help="Conformal miscoverage level (1-alpha coverage)"
    ),
) -> int:
    """Deductive coding run (v5 §8): three-view scoring + calibrated thresholds."""
    return _run_async(_coding_run_coroutine(codebook_version_id, project_id, alpha))


def _coding_run_coroutine(codebook_version_id: str, project_id: str | None, alpha: float):
    from corpus_kb.tools.research_tools import coding_run as coding_run_tool

    async def _run() -> int:
        import json

        async with wired_pool() as wired:
            # The shared tool advances ALL projections (research + documents
            # + embeds) — the same post-condition every other surface gets.
            summary = await coding_run_tool(
                wired.pool,
                codebook_version_id,
                project_id=project_id,
                tenant_id=DEFAULT_TENANT,
                cal_alpha=alpha,
            )
            print(json.dumps(summary, indent=2, default=str))
            return 0 if summary.get("status") == "success" else 1

    return _run()


@coding_app.command("inductive")
def coding_inductive(
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
) -> int:
    """Inductive pass (v5 §9): summaries -> UMAP/HDBSCAN -> proposed codes."""
    return _run_async(_inductive_coroutine(project_id))


def _inductive_coroutine(project_id: str | None):
    from corpus_kb.tools.research_tools import inductive_run as inductive_run_tool

    async def _run() -> int:
        import json

        async with wired_pool() as wired:
            summary = await inductive_run_tool(
                wired.pool,
                project_id=project_id,
                tenant_id=DEFAULT_TENANT,
            )
            print(json.dumps(summary, indent=2, default=str))
            if summary.get("status") == "unavailable":
                return 2
            return 0 if summary.get("status") == "success" else 1

    return _run()


@codebook_app.command("promote")
def codebook_promote(
    proposed_id: int = typer.Option(..., "--proposed-id", help="research_proposed_codes id"),
    name: str = typer.Option(..., "--name", help="Human-approved code name"),
    definition: str = typer.Option(..., "--definition", help="Human-approved definition"),
    inclusion: str = typer.Option("", "--inclusion", help="Inclusion criteria"),
    exclusion: str = typer.Option("", "--exclusion", help="Exclusion criteria"),
    label: str = typer.Option(None, "--label", help="Version label hint (default: cluster terms)"),
    tau_dup: float = typer.Option(
        None, "--tau-dup", help="Duplicate-gate threshold (default: calibrated/config)"
    ),
) -> int:
    """Promote one proposed code into a NEW codebook version (human gate)."""
    return _run_async(
        _promote_coroutine(proposed_id, name, definition, inclusion, exclusion, label, tau_dup)
    )


def _promote_coroutine(
    proposed_id: int,
    name: str,
    definition: str,
    inclusion: str,
    exclusion: str,
    label: str | None,
    tau_dup: float | None,
):
    from corpus_kb.tools.research_tools import codebook_promote as promote_tool

    async def _run() -> int:
        import json

        async with wired_pool() as wired:
            # tau_dup None means "calibrated/config default" — the shared
            # tool resolves it exactly as the MCP/HTTP surfaces do.
            result = await promote_tool(
                wired.pool,
                proposed_id,
                name,
                definition,
                inclusion=inclusion,
                exclusion=exclusion,
                label=label,
                tau_dup=tau_dup,
                tenant_id=DEFAULT_TENANT,
            )
            print(json.dumps(result, indent=2, default=str))
            if result.get("status") == "promoted":
                return 0
            if result.get("status") == "duplicate_blocked":
                return 3
            return 1

    return _run()
