"""Study query commands: ingest, search, similar, ask, sql, events.

Each command picks the study (4-step selection), turns its server up,
and sends one op through the op runner (issue #351). ``sql`` posts to
the read-only route and passes the engine's refusal through as one clear
message.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import op_client
import op_runner
import post_search
import study_commands
import study_server
from engine_paths import plugin_data_dir


def _record_since_status(envelope: dict) -> None:
    """Accumulate ingested/failed counts until the next status prints them."""
    path = plugin_data_dir() / "since_status.json"
    counts = {"ingested": 0, "failed": 0}
    if path.is_file():
        with contextlib.suppress(OSError, ValueError):
            counts.update(json.loads(path.read_text(encoding="utf-8")))
    results = envelope.get("results")
    if isinstance(results, list) and results:
        counts["ingested"] += sum(
            1 for row in results if isinstance(row, dict) and row.get("status") == "success"
        )
        counts["failed"] += sum(
            1 for row in results if isinstance(row, dict) and row.get("status") != "success"
        )
    elif envelope.get("status") == "success":
        try:
            counts["ingested"] += int(envelope.get("total_documents") or 1)
        except (TypeError, ValueError):
            counts["ingested"] += 1
    else:
        counts["failed"] += 1
    errors = envelope.get("errors")
    if isinstance(errors, list):
        counts["failed"] += len(errors)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counts), encoding="utf-8")


def cmd_ingest(args) -> int:
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    path = str(Path(args.path).expanduser())
    target = Path(path)
    try:
        if target.is_dir():
            envelope = op_runner.run_op(
                cli, "ingest_directory", [f"directory_path={path}"], study_server.base_url(study)
            )
        elif target.is_file():
            envelope = op_runner.run_op(
                cli, "ingest_file", [f"file_path={path}"], study_server.base_url(study)
            )
        else:
            print(f"not ok: no file or folder at {path}")
            return 1
    except op_runner.OpError as exc:
        print(f"not ok: {exc}")
        return 2
    except op_client.HttpError as exc:
        print(f"not ok: {exc}")
        return 1
    _record_since_status(envelope)
    ok = envelope.get("status") == "success"
    if ok:
        chunks = envelope.get("chunk_count")
        entities = envelope.get("entity_count")
        print(f"ok: ingested {Path(path).name} into '{slug}' "
              f"(chunks: {chunks}, entities: {entities}).")
        if envelope.get("degraded"):
            print("Some chunks have no vector (degraded). Run `corpus-rag reembed`.")
    else:
        print(f"not ok: ingest into '{slug}' failed.")
        print(json.dumps(envelope, indent=2, default=str)[:2000])
    return 0 if ok else 1


def _print_hits(envelope: dict) -> int:
    hits = envelope.get("result")
    if not isinstance(hits, list) or not hits:
        print("No matches.")
        return 0
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        score = hit.get("score", "?")
        source = hit.get("source") or hit.get("document") or hit.get("doc_id") or "?"
        print(f"[{score}] {source}  chunk {hit.get('chunk_id', '?')}")
        snippet = str(hit.get("snippet") or hit.get("text") or "").strip()
        print(f"    {' '.join(snippet.split())[:240]}")
    excluded = envelope.get("degraded_excluded")
    if excluded:
        print(f"Degraded (no-vector) chunks excluded: {excluded}")
    return 0


def cmd_search(args) -> int:
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    if args.mode == "context":
        name = "search_context"
        pairs = [
            f"query={args.query}",
            f"k={args.k}",
            f"context_chunks={args.context_chunks}",
        ]
    else:
        name = "search"
        pairs = [f"query={args.query}", f"k={args.k}"]
    try:
        envelope = op_runner.run_op(cli, name, pairs, study_server.base_url(study))
    except op_runner.OpError as exc:
        print(f"not ok: {exc}")
        return 2
    except op_client.HttpError as exc:
        print(f"not ok: {exc}")
        return 1
    print(f"Search '{slug}':")
    code = _print_hits(envelope)
    # The generic post_search extension point (issue #355): registered
    # commands may print more, like derived insights. Fail-open and quiet
    # when nothing is registered or the study is not theirs.
    post_search.run_registered(slug, args.query)
    return code


def cmd_similar(args) -> int:
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    pairs = [f"chunk_id={args.chunk_id}", f"k={args.k}"]
    try:
        envelope = op_runner.run_op(cli, "search_similar", pairs, study_server.base_url(study))
    except op_runner.OpError as exc:
        print(f"not ok: {exc}")
        return 2
    except op_client.HttpError as exc:
        print(f"not ok: {exc}")
        return 1
    print(f"Chunks similar to {args.chunk_id} in '{slug}':")
    return _print_hits(envelope)


def cmd_ask(args) -> int:
    cli, _slug, study = study_commands.scoped_study(args.study_slug)
    try:
        envelope = op_runner.run_op(
            cli, "ask", [f"question={args.question}"], study_server.base_url(study)
        )
    except op_runner.OpError as exc:
        print(f"not ok: {exc}")
        return 2
    except op_client.HttpError as exc:
        print(f"not ok: {exc}")
        return 1
    result = envelope.get("result")
    answer = result.get("answer") if isinstance(result, dict) else None
    if answer:
        print(str(answer))
    else:
        print(json.dumps(envelope, indent=2, default=str)[:2000])
    citations = result.get("citations") if isinstance(result, dict) else None
    if isinstance(citations, list) and citations:
        print("\nCitations:")
        for row in citations:
            if isinstance(row, dict):
                print(f"  [S:{row.get('source', row.get('doc_id', '?'))}] "
                      f"chunk {row.get('chunk_id', '?')}")
    if envelope.get("degraded_excluded"):
        print("Some sources were skipped for missing vectors.")
    return 0


def cmd_sql(args) -> int:
    _cli, _slug, study = study_commands.scoped_study(args.study_slug)
    url = study_server.base_url(study) + "/api/query/sql"
    try:
        envelope = op_client.post_json(url, {"sql": args.query}, timeout=300)
    except op_client.HttpError as exc:
        # The engine's read-only guard refuses writes; pass it through.
        print(f"not ok: {exc}")
        return 1
    rows = envelope.get("result", envelope)
    print(json.dumps(rows, indent=2, default=str))
    return 0


def cmd_events(args) -> int:
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    try:
        envelope = op_runner.run_op(cli, "events_summary", [], study_server.base_url(study))
    except op_runner.OpError as exc:
        print(f"not ok: {exc}")
        return 2
    except op_client.HttpError as exc:
        print(f"not ok: {exc}")
        return 1
    print(f"Event-store health for '{slug}':")
    print(json.dumps(envelope.get("result", envelope), indent=2, default=str))
    return 0
