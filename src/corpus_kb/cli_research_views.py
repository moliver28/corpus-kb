"""Notebook view commands (zero-drift Wave 3B split).

Moved byte-identical from cli.py (the 250-line limit): the three read-only
research views over ``research.notebook_views`` —

  * ``corpus-kb research evidence``
  * ``corpus-kb research uncoded``
  * ``corpus-kb research overlap``

— plus their human-readable printers. Registered by cli_research in the
original help-output order (evidence, uncoded, overlap).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import typer

from corpus_kb.cli_support import _pid, _run_async, _tenant, cast_dict

if TYPE_CHECKING:
    from corpus_kb.projections.research._embed import ResearchEmbedder


def register(research_app: typer.Typer) -> None:
    """Attach evidence/uncoded/overlap in help-output order."""
    research_app.command("evidence")(research_evidence)
    research_app.command("uncoded")(research_uncoded)
    research_app.command("overlap")(research_overlap)


def research_evidence(
    code: str = typer.Argument(..., help="Code name or UUID"),
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
    version_id: str = typer.Option(None, "--codebook-version", help="Codebook version UUID"),
    limit: int = typer.Option(200, "--limit", min=1, help="Max units per tab"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> int:
    """Evidence for code X: ranked units with disagreement/moderator/conformal flags."""
    return _run_async(_evidence_coroutine(code, project_id, version_id, limit, json_output))


def _evidence_coroutine(
    code: str,
    project_id: str | None,
    version_id: str | None,
    limit: int,
    json_output: bool,
):
    import json

    async def _run() -> int:
        from corpus_kb.cli_store import wired_store
        from corpus_kb.research.notebook_views import evidence_for_code

        async with wired_store() as wired:
            result = await evidence_for_code(
                wired.store,
                _tenant(),
                code,
                project_id=_pid(project_id),
                version_id=_pid(version_id),
                limit=limit,
            )
        if json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_evidence_result(result)
        return 0 if result.get("status") == "ok" else 1

    return _run()


def _print_evidence_result(result: dict[str, object]) -> None:
    from corpus_kb.research import guide_copy

    if result.get("status") != "ok":
        print(str(result.get("message", "")))
        return
    print(f"{guide_copy.NOTEBOOK_EVIDENCE_FOR} {result.get('code')}: {result.get('n_units')} units")
    tabs = cast_dict(result.get("tabs"))
    for tab in ("explicit", "question_dependent"):
        print(f"\n{tab}:")
        entries = cast("list[dict[str, object]]", tabs.get(tab) or [])
        for entry in entries:
            flags = []
            if entry.get("disagreement"):
                flags.append("disagreement")
            if entry.get("moderator_introduced"):
                flags.append("moderator_introduced")
            flag_text = f" ({', '.join(flags)})" if flags else ""
            print(
                f"  unit {entry.get('unit_id')} [{entry.get('confidence')}] "
                f"set_size={entry.get('conformal_set_size')}{flag_text}: "
                f"{str(entry.get('text'))[:120]}"
            )


def research_uncoded(
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
    version_id: str = typer.Option(None, "--codebook-version", help="Codebook version UUID"),
    k: int = typer.Option(25, "--k", min=1, help="Max units to surface"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> int:
    """Uncoded/residual units ranked by nearest-code margin (missing-code radar)."""
    return _run_async(_uncoded_coroutine(project_id, version_id, k, json_output))


def _uncoded_coroutine(project_id: str | None, version_id: str | None, k: int, json_output: bool):
    import json

    async def _run() -> int:
        from corpus_kb.cli_store import wired_store
        from corpus_kb.research.notebook_views import uncoded_units

        async with wired_store(embedder=True) as wired:
            result = await uncoded_units(
                wired.store,
                cast("ResearchEmbedder", wired.embedder),
                _tenant(),
                project_id=_pid(project_id),
                version_id=_pid(version_id),
                k=k,
            )
        if json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_uncoded_result(result)
        return 0 if result.get("status") == "ok" else 1

    return _run()


def _print_uncoded_result(result: dict[str, object]) -> None:
    from corpus_kb.research import guide_copy

    if result.get("status") != "ok":
        print(str(result.get("message", "")))
        return
    print(guide_copy.NOTEBOOK_UNCODED_HEADER)
    for unit in cast("list[dict[str, object]]", result.get("units") or []):
        margin = cast("float", unit.get("margin", 0.0))
        print(f"  unit {unit.get('unit_id')} margin={margin:.3f}: {str(unit.get('text'))[:120]}")


def research_overlap(
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
    version_id: str = typer.Option(None, "--codebook-version", help="Codebook version UUID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> int:
    """Code overlap: flagged pairs + shared-unit confusion + borderline units."""
    return _run_async(_overlap_coroutine(project_id, version_id, json_output))


def _overlap_coroutine(project_id: str | None, version_id: str | None, json_output: bool):
    import json

    async def _run() -> int:
        from corpus_kb.cli_store import wired_store
        from corpus_kb.research.notebook_views import overlap_view

        async with wired_store() as wired:
            result = await overlap_view(
                wired.store,
                _tenant(),
                project_id=_pid(project_id),
                version_id=_pid(version_id),
            )
        if json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_overlap_result(result)
        return 0 if result.get("status") == "ok" else 1

    return _run()


def _print_overlap_result(result: dict[str, object]) -> None:
    from corpus_kb.research import guide_copy

    if result.get("status") != "ok":
        print(str(result.get("message", "")))
        return
    print(guide_copy.NOTEBOOK_OVERLAP_HEADER)
    pairs = cast("list[dict[str, object]]", result.get("flagged_pairs") or [])
    if pairs:
        for pair in pairs:
            cos = cast("float", pair.get("cos", 0.0))
            print(f"  {pair.get('a')} <-> {pair.get('b')}: cos={cos:.3f}")
    else:
        print("  none above tau_overlap")
    borderline = cast("list[dict[str, object]]", result.get("borderline_units") or [])
    print(f"\nborderline top-2-margin units (< {result.get('delta_amb')}): {len(borderline)}")
    for unit in borderline:
        print(f"  unit {unit.get('unit_id')}: {str(unit.get('text'))[:100]}")
