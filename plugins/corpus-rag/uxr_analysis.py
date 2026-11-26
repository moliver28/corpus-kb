"""The ``uxr`` rollup handlers: validate-rows, rollup, contract (#355).

Validate rows and store the failures as ``unverified``, count stances
per question with researcher-line precedence, and compare the rollup
with the report tool's ``evidence-weight`` output.

Stdlib only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import uxr_commands
import uxr_merge
import uxr_rollup
import uxr_rows
import uxr_store
import uxr_summaries
from uxr_common import UxrError, json_payload, root_of, slug_for

BLOCK_LIMIT = 3  # kept for callers that import it from here


def cmd_uxr_validate_rows(args) -> int:
    """Check stance enum, unit ids, evidence, and each quote."""
    rows = json_payload(args.payload, "validate-rows")
    conn = uxr_store.connect(slug_for(args))
    report = uxr_rows.validate_rows(conn, rows, store=bool(args.store))
    for entry in report["rows"]:
        mark = "ok" if entry["ok"] else "unverified"
        problems = "; ".join(entry["problems"])
        line = f"{mark}: {entry['session']}/{entry['unit_id']} {entry['stance']}"
        if entry["quote_class"]:
            line += f" (quote {entry['quote_class']})"
        print(line + (f" — {problems}" if problems else ""))
    for near in report["near_misses"]:
        print(f"near miss logged: {near}")
    print(
        f"rows: {report['stored']} verified, {report['unverified']} unverified"
        f"{' (stored)' if args.store else ' (not stored)'}"
    )
    return 0


def cmd_uxr_rollup(args) -> int:
    """Count stances per question from verified rows; researcher lines win."""
    conn = uxr_store.connect(slug_for(args))
    rows = uxr_store.rows(conn)
    researcher = uxr_rollup.researcher_from_dir(root_of(args))
    report = uxr_rollup.build(rows, researcher)
    print_rollup(report)
    evidence_rows = uxr_merge.coded_rows(args.study_slug)
    evidence_section = uxr_merge.render_coded_evidence(evidence_rows)
    if evidence_section:
        print(evidence_section)
    if uxr_rollup.over_limit(report, args.max_unverified):
        share = report["totals"]["share"]
        print(
            f"not ok: the unverified share {share:.0%} is over the limit {args.max_unverified:.0%}."
        )
        return 1
    print("ok: the unverified share is within the limit.")
    return 0


def print_rollup(report: dict) -> None:
    """The rollup lines, units in RQ, H, A order, researcher marks last."""
    for unit, stances in report["units"].items():
        parts = [f"{stance} {count}" if count > 1 else stance for stance, count in stances.items()]
        line = f"{unit}: {', '.join(parts)}"
        researcher = report["researcher"].get(unit)
        if researcher:
            line += f" (researcher: {researcher})"
        print(line)
    for unit, line in report["researcher"].items():
        if unit not in report["units"]:
            print(f"{unit}: (researcher: {line})")
    totals = report["totals"]
    print(
        f"totals: {totals['verified']} verified, {totals['unverified']} "
        f"unverified ({totals['share']:.0%})"
    )


def cmd_uxr_contract_test(args) -> int:
    """Compare the rollup with the report tool's evidence-weight output."""
    conn = uxr_store.connect(slug_for(args))
    rows = uxr_store.rows(conn)
    report = uxr_rollup.rollup_json(
        uxr_rollup.build(rows, uxr_rollup.researcher_from_dir(root_of(args)))
    )
    evidence, degraded = _evidence(args, report)
    if evidence is None:
        return 1
    if degraded:
        print("evidence check ran in degraded mode: no report tool is set,")
        print("so the rollup is its own evidence. Configure uxr_report_tool")
        print("or pass --evidence-file for a real parity check.")
    diffs = uxr_rollup.contract_diff(report, evidence)
    if diffs:
        print("not ok: the report tool output differs from the rollup.")
        for diff in diffs:
            print(f"  {diff}")
        return 1
    print("ok: contract parity; the rollup matches the report tool.")
    return 0


def _evidence(args, payload: dict) -> tuple[dict | None, bool]:
    """The evidence-weight JSON: the file flag, else the configured tool."""
    if args.evidence_file:
        try:
            data = json.loads(Path(args.evidence_file).read_text(encoding="utf-8"))
        except OSError as exc:
            print(f"not ok: cannot read the evidence file: {exc}")
            return None, False
        except ValueError as exc:
            print(f"not ok: the evidence file is not valid JSON: {exc}")
            return None, False
        return (data, False) if isinstance(data, dict) else (None, False)
    tool = os.environ.get("CLAUDE_PLUGIN_OPTION_UXR_REPORT_TOOL", "")
    if not tool:
        # D7(d): the rollup is the default evidence source. Honest
        # degraded note instead of a dead end; parity stays exit 1 when
        # a real tool or file is configured and disagrees.
        return payload, True
    try:
        return uxr_rollup.evidence_from_tool(tool, payload), False
    except uxr_rollup.ContractError as exc:
        print(f"not ok: {exc}")
        return None


def cmd_uxr_migrate(args) -> int:
    """The deprecated ``uxr migrate`` alias; one pointer line, then import."""
    print("note: 'migrate' is deprecated. The command is 'uxr import'; running it.")
    return uxr_summaries.cmd_uxr_import(args)


UXR_HANDLERS = {
    "consent": uxr_commands.cmd_uxr_consent,
    "ingest-plan": uxr_commands.cmd_uxr_ingest_plan,
    "classify": uxr_commands.cmd_uxr_classify,
    "confirm-classes": uxr_commands.cmd_uxr_confirm_classes,
    "stubs": uxr_commands.cmd_uxr_stubs,
    "check-quote": uxr_commands.cmd_uxr_check_quote,
    "validate-rows": cmd_uxr_validate_rows,
    "rollup": cmd_uxr_rollup,
    "contract-test": cmd_uxr_contract_test,
    "write-themes": uxr_summaries.cmd_uxr_write_themes,
    "apply-summary": uxr_summaries.cmd_uxr_apply_summary,
    "insights": uxr_summaries.cmd_uxr_insights,
    "import": uxr_summaries.cmd_uxr_import,
    "migrate": cmd_uxr_migrate,
    "post-search": uxr_summaries.cmd_uxr_post_search,
}


def cmd_uxr(args) -> int:
    """The uxr dispatcher; prints one honest line per failure."""
    try:
        return UXR_HANDLERS[args.uxr_command](args)
    except UxrError as exc:
        print(f"not ok: {exc.message}")
        return exc.code
