"""``corpus-kb research report`` (zero-drift Wave 3B split).

Moved byte-identical from cli.py (the 250-line limit): the governance
report command (v5 §11/§13/§14 in ONE artifact), its coroutine, and the
novice/expert printers. cli.py re-exports ``_print_report`` so the
demo-offline test keeps its import path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import typer

from corpus_kb.cli_support import _pid, _run_async, _tenant, cast_dict, cast_list, wired_pool


def research_report(
    codebook_version_id: str = typer.Option(
        None, "--codebook-version", help="Version (default: latest)"
    ),
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
    level: str = typer.Option(
        "novice", "--level", help="Presentation level: novice (traffic lights + guidance) or expert"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable full schema"),
) -> int:
    """Emit the governance report (v5 §11/§13/§14 in ONE artifact)."""
    return _run_async(_report_coroutine(codebook_version_id, project_id, level, json_output))


def _report_coroutine(
    codebook_version_id: str | None,
    project_id: str | None,
    level: str,
    json_output: bool,
):
    import json

    from corpus_kb.research.governance_report import to_dict, validate_required
    from corpus_kb.research.report_runner import build_report

    async def _run() -> int:
        async with wired_pool() as wired:
            report = await build_report(
                wired.pool,
                _tenant(),
                _pid(codebook_version_id),
                level=level,
                run_manifest={"project_id": str(project_id) if project_id else None},
            )
            payload = to_dict(report)
            missing = validate_required(payload)
            if missing:
                print(f"report missing required fields: {missing}")
                return 2
            if json_output:
                print(json.dumps(payload, indent=2, default=str))
            else:
                _print_report(report, level)
            return 0

    return _run()


def _print_report(report: object, level: str) -> None:
    from corpus_kb.research import guide_copy
    from corpus_kb.research.governance_report import (
        ResearchReport,
        _mapping,
        novice_view,
        to_dict,
    )

    print(guide_copy.REPORT_TITLE)
    print(guide_copy.REPORT_SUBTITLE)
    is_report = isinstance(report, ResearchReport)
    payload = to_dict(report) if is_report else cast_dict(report)
    if level == guide_copy.LEVEL_NOVICE and is_report:
        stored = payload.get("novice_view")
        view = _mapping(stored) if stored else novice_view(report)
        actions = _mapping(view).get("next_actions", [])
        for _name, light in _mapping(_mapping(view).get("traffic_lights")).items():
            level_text = str(_mapping(light).get("level", ""))
            message = str(_mapping(light).get("message", ""))
            print(f"[{level_text.upper()}] {message}")
        print()
        for _name, text in _mapping(_mapping(view).get("plain_language")).items():
            print(f"- {text}")
        print()
        print(guide_copy.NEXT_ACTIONS_HEADER)
        for action in cast_list(actions):
            print(f"-> {action}")
        print()
        anchors = cast(
            "Mapping[str, str]", _mapping(view).get("doc_links") or guide_copy.REPORT_DOC_ANCHORS
        )
        for section, link in anchors.items():
            print(f"guide[{section}]: {link}")
    else:
        for key in (
            "isr_pooled",
            "coverage_explicit",
            "coverage_qdep",
            "gray_zone_share",
            "tier3_disagreement_rate",
        ):
            print(f"{key}: {payload.get(key)}")
        for key in (
            "residual",
            "coverage_curve",
            "missing_codes",
            "cluster_stability",
            "overlap",
            "keywords",
            "kappa_alpha",
            "g3_audit",
            "conformal",
            "codebook_diff",
            "run_manifest",
        ):
            print(f"{key}: {_mapping(payload.get(key)) or payload.get(key)}")


def register(research_app: typer.Typer) -> None:
    """Attach ``report`` to the research sub-app (help order: last)."""
    research_app.command("report")(research_report)
