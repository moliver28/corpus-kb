"""Text rendering for the ingest front door CLI (split from cli_ingest.py).

Pure formatting, zero I/O beyond print: the per-file ingest lines, the
reembed summary, and the status report table. Keeping the views here lets
cli_ingest.py stay near the 250-line soft limit with the control flow.
"""

from __future__ import annotations

RUN_LINE = "{status:>9}  chunks={chunk_count:<5} entities={entity_count:<4} {path}"


def string_list(value: object) -> list[str]:
    """A JSON list-of-strings field or nothing (honest narrowing, no Any)."""
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def print_ingest_result(result: dict[str, object]) -> None:
    """One pipeline result as a line plus its warnings/errors, honestly."""
    print(
        RUN_LINE.format(
            status=str(result.get("status", "error")),
            chunk_count=result.get("chunk_count", "?"),
            entity_count=result.get("entity_count", "?"),
            path=str(result.get("path", "?")),
        )
    )
    # Honesty envelope: which embedding technique actually ran, and why a
    # requested one (late chunking) fell back.
    if result.get("embedding_mode") is not None:
        print(f"    embedding: {result['embedding_mode']}")
    if result.get("embedding_note") is not None:
        print(f"    embedding note: {result['embedding_note']}")
    for warning in string_list(result.get("warnings")):
        print(f"    warning: {warning}")
    if str(result.get("status", "")) != "success":
        for error in string_list(result.get("errors")):
            print(f"    error: {error}")


def print_reembed_summary(summary: dict[str, object]) -> None:
    """The honest scanned/fixed/failed line (+ degraded-provider warning)."""
    status = str(summary.get("status", "error"))
    print(
        f"reembed: {status} scanned={summary.get('scanned', 0)} "
        f"fixed={summary.get('fixed', 0)} failed={summary.get('failed', 0)}"
    )
    if summary.get("embedding_mode") is not None:
        print(f"    embedding: {summary['embedding_mode']}")
    if summary.get("embedding_note") is not None:
        print(f"    embedding note: {summary['embedding_note']}")
    if summary.get("warning"):
        print(f"    warning: {summary['warning']}")
    if summary.get("reason"):
        print(f"    reason: {summary['reason']}")
    if summary.get("remedy"):
        print(f"    remedy: {summary['remedy']}")


def print_status_report(report: dict[str, object]) -> None:
    """The status surface for humans: degraded counts + the run ledger."""
    if str(report.get("status")) == "unavailable":
        print(f"ingest-status: UNAVAILABLE — {report.get('reason')}")
        print(f"    remedy: {report.get('remedy')}")
        return
    if str(report.get("status")) == "error":
        print(f"ingest-status: ERROR — {report.get('message')}")
        if report.get("remedy"):
            print(f"    remedy: {report.get('remedy')}")
        return
    degraded = report.get("degraded")
    degraded_map = degraded if isinstance(degraded, dict) else {}
    print(
        f"degraded chunks={degraded_map.get('chunks', 0)} "
        f"vectors={degraded_map.get('chunk_vectors', 0)}; "
        f"incomplete runs={report.get('incomplete_runs', 0)}"
    )
    runs = report.get("runs")
    runs_list = [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []
    if not runs_list:
        print("no ingest runs recorded yet")
        return
    for run in runs_list:
        status = str(run.get("status", "?"))
        started = str(run.get("started_at", "?"))[:19]
        source = str(run.get("source_type") or "-")
        run_id = str(run.get("run_id"))[:8]
        project = str(run.get("project_id") or "-")[:8]
        print(f"{status:>9}  {started}  {source:<10} {run_id}  {project}")
        if run.get("error"):
            print(f"          error: {str(run['error'])[:160]}")
