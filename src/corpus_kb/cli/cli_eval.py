"""The ``corpus-kb eval`` front door (UX program Wave 5).

A lifecycle command family — NOT a catalogue operation, so
``surface_registry`` and the parity gates are untouched (the
``cli_quickstart`` precedent).

Commands:

  * ``corpus-kb eval build-golden`` — write a golden JSONL from
    human-reviewed codings (``--from-assignments``, needs the database)
    or validate an existing one (``--validate <path>``).
  * ``corpus-kb eval run`` — score a golden file: recall@k, MRR and
    nDCG@10 for retrieval, deepeval answer metrics when asked for.

HONEST EXIT CODES: ``0`` the command answered (an empty golden set is
still 0 — status ``empty``), ``1`` bad input or failure, ``3`` the
database is unreachable (the shared ingest plumbing maps connection
failures there). Every outcome is PRINTED or emitted as ``--json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync
from corpus_kb.eval.golden import GoldenRecord, golden_summary

EXIT_OK = 0
EXIT_FAILED = 1

eval_app = typer.Typer(help="Offline evaluation: golden sets, retrieval and answer metrics")

BUILD_HELP = (
    "Build a golden JSONL from human-reviewed codings (--from-assignments) "
    "or validate an existing one (--validate)"
)


def _write_golden(records: list[GoldenRecord], out: Path) -> int:
    """Write one JSON object per line; return the record count."""
    lines = [
        json.dumps(
            {
                "query": record.query,
                "relevant_chunk_ids": list(record.relevant_chunk_ids),
                "answer": record.answer,
            }
        )
        for record in records
    ]
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def _print_summary(records: list[GoldenRecord], out: Path | None) -> None:
    summary = golden_summary(records)
    print(f"records: {summary['records']}")
    print(f"with answer: {summary['with_answer']}")
    print(f"without answer: {summary['without_answer']}")
    if out is not None:
        print(f"wrote: {out}")


@eval_app.command("build-golden", help=BUILD_HELP)
def build_golden(
    from_assignments: bool = typer.Option(
        False, "--from-assignments", help="Build from reviewed research_assignments rows"
    ),
    validate: str | None = typer.Option(
        None, "--validate", help="Validate this golden JSONL file and print the summary"
    ),
    out: str = typer.Option("golden.jsonl", "--out", help="Output JSONL path"),
    project: str | None = typer.Option(
        None, "--project", help="Project UUID whose reviewed codings feed the golden set"
    ),
) -> int:
    """Create or check a golden set file."""
    if from_assignments == (validate is not None):
        print("ERROR: choose exactly one of --from-assignments or --validate <path>")
        raise typer.Exit(code=EXIT_FAILED)
    if validate is not None:
        return _validate_golden(Path(validate))
    return _run_sync(lambda: _build_from_assignments(Path(out), project))


def _validate_golden(path: Path) -> int:
    from corpus_kb.eval.golden import load_golden

    try:
        records = load_golden(path)
    except Exception as exc:
        # Not inside _run_sync here, so a non-zero exit must be raised
        # (typer ignores plain int returns — the cli_ingest _finish rule).
        print(f"ERROR: {exc}")
        raise typer.Exit(code=EXIT_FAILED) from None
    _print_summary(records, None)
    return EXIT_OK


ASSIGNMENTS_SQL = """
SELECT cr.code_id, cr.name, cr.brief_definition,
       array_agg(DISTINCT ra.unit_id) AS unit_ids
FROM research_assignments ra
JOIN code_registry cr
  ON cr.code_id = ra.code_id AND cr.tenant_id = ra.tenant_id
JOIN research_units u
  ON u.unit_id = ra.unit_id AND u.tenant_id = ra.tenant_id
WHERE ra.tenant_id = $1
  AND ra.status IN ('confirmed', 'overridden')
  AND u.project_id = $2::uuid
GROUP BY cr.code_id, cr.name, cr.brief_definition
"""


async def _build_from_assignments(out: Path, project: str | None) -> int:
    from corpus_kb.cli.cli_support import _tenant
    from corpus_kb.eval.golden import build_golden_from_assignments
    from corpus_kb.storage.tenant_conn import tenant_connection

    if project is None:
        print("ERROR: --from-assignments needs --project <uuid>")
        return EXIT_FAILED
    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        async with tenant_connection(pool, str(_tenant())) as conn:
            rows = await conn.fetch(ASSIGNMENTS_SQL, str(_tenant()), project)
    finally:
        await pool.close()
    records = build_golden_from_assignments([dict(row) for row in rows])
    count = _write_golden(records, out)
    print(f"built {count} golden record(s) from reviewed assignments")
    _print_summary(records, out)
    return EXIT_OK


@eval_app.command("run", help="Score a golden set: retrieval metrics plus answer metrics")
def run(
    golden: str = typer.Option(..., "--golden", help="The golden JSONL file to score"),
    k: int = typer.Option(10, "--k", min=1, help="Retrieval depth for recall@k"),
    report: str | None = typer.Option(
        None, "--report", help="Write the JSON envelope to this file too"
    ),
    skip_answer_metrics: bool = typer.Option(
        False, "--skip-answer-metrics", help="Skip the answer section (retrieval only)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
    project: str | None = typer.Option(
        None, "--project", help="Project UUID the retrieval search is scoped to"
    ),
) -> int:
    """Run the offline evaluation."""
    return _run_sync(
        lambda: _run_flow(
            Path(golden),
            k,
            None if report is None else Path(report),
            skip_answer_metrics,
            json_output,
            project,
        )
    )


async def _run_flow(
    golden: Path,
    k: int,
    report: Path | None,
    skip_answer_metrics: bool,
    json_output: bool,
    project: str | None,
) -> int:
    from corpus_kb.eval.golden import GoldenFormatError, load_golden
    from corpus_kb.eval.report import to_envelope
    from corpus_kb.eval.runner import NIL_PROJECT_ID
    from corpus_kb.eval.runner import run_eval as run_eval_tool

    try:
        records = load_golden(golden)
    except GoldenFormatError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        eval_report = await run_eval_tool(
            records,
            cfg,
            pool,
            k=k,
            skip_answer_metrics=skip_answer_metrics,
            project_id=project if project is not None else NIL_PROJECT_ID,
        )
    finally:
        await pool.close()
    envelope = to_envelope(eval_report)
    _emit(envelope, json_output)
    if report is not None:
        report.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
        print(f"report: {report}")
    return EXIT_OK


def _emit(envelope: dict[str, object], json_output: bool) -> None:
    """Print the exact envelope, or a human summary."""
    if json_output:
        print(json.dumps(envelope, indent=2, default=str))
        return
    result = envelope.get("result")
    body = result if isinstance(result, dict) else {}
    print(f"eval status: {envelope.get('status')}")
    print(f"golden: {body.get('golden')}")
    retrieval = body.get("retrieval")
    if isinstance(retrieval, dict):
        print(
            f"retrieval: recall@k={retrieval.get('recall_at_k')} "
            f"mrr={retrieval.get('mrr')} ndcg@10={retrieval.get('ndcg_at_10')} "
            f"(k={retrieval.get('k')}, n={retrieval.get('n_records')})"
        )
    answer = body.get("answer")
    if isinstance(answer, dict):
        if "skipped" in answer:
            print(f"answer: skipped ({answer.get('skipped')})")
        else:
            print(f"answer: {answer}")
    degraded = envelope.get("degraded_excluded")
    if isinstance(degraded, dict) and degraded.get("reasons"):
        print(f"degraded_excluded: {degraded}")
