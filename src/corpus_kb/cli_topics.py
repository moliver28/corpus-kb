"""The ``corpus-kb topics`` front door (Wave 4).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb topics-build`` — the offline BERTopic build over the
    STORED chunk embeddings (the same handler the MCP + HTTP + socket
    surfaces call).
  * ``corpus-kb topics-list``  — the built topics, largest first.
  * ``corpus-kb topics-trends`` — the temporal trends build (analysis
    program WP-4): per-topic series over binned document dates.

HONEST EXIT CODES (stable for scripting, documented in each command's
help): ``0`` the command answered (including the honest no-op outcomes:
disabled feature, insufficient data, empty list), ``1`` the build
failed, ``3`` configuration state (feature disabled is 0 by design — see
below; migration missing, ``[topics]`` extra missing, unreachable
database). Every outcome is PRINTED or emitted as ``--json``.

A DISABLED feature exits 0: the documented default state is "dormant",
not broken — the printed envelope names the flag and the remedy. The
dependency error is exit 3 (the config/environment bucket, matching the
``dependency_missing`` taxonomy row).
"""

from __future__ import annotations

import json

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 3


def register(app: typer.Typer) -> None:
    """Attach the topics commands to the main Typer app."""
    app.command("topics-build")(topics_build)
    app.command("topics-list")(topics_list)
    # Analysis program trends op (WP-4): temporal topic trends.
    app.command("topics-trends")(topics_trends)


def _exit_for(result: dict[str, object], *, failed_status: str = "error") -> int:
    """Map a topics envelope to the honest exit code."""
    status = str(result.get("status", failed_status))
    if status in ("disabled", "unavailable", "insufficient_data", "success"):
        return EXIT_CONFIG if status == "unavailable" else EXIT_OK
    return EXIT_FAILED


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status == "success":
        print(f"topics: {result.get('topic_count', 0)} built; status {status}")
        for topic in _topic_list(result):
            label = topic.get("reduced_label") or topic.get("label")
            print(f"  [{topic.get('topic_id')}] {label} ({topic.get('size')} chunks)")
        return
    print(f"topics: {status}")
    reason = result.get("reason")
    if reason:
        print(f"  {reason}")
    remedy = result.get("remedy")
    if remedy:
        print(f"  remedy: {remedy}")


def _topic_list(result: dict[str, object]) -> list[dict[str, object]]:
    raw = result.get("topics", [])
    if not isinstance(raw, list):
        return []
    return [topic for topic in raw if isinstance(topic, dict)]


async def _build_flow(json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.topics_tools import topics_build
    from corpus_kb.topics.engine import TopicDependencyError

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await topics_build(pool, cfg)
    except TopicDependencyError as exc:
        _emit(
            {"status": "dependency_missing", "reason": str(exc), "remedy": exc.remediation},
            json_output,
        )
        return EXIT_CONFIG
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


async def _list_flow(limit: int, json_output: bool) -> int:
    from corpus_kb.tools.topics_tools import topics_list

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await topics_list(pool, cfg, limit=limit)
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


def topics_build(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Cluster the stored chunk embeddings into topics (offline BERTopic).

    Requires topics.enabled: true and the [topics] extra. Exit codes:
    0 built (or an honest no-op: disabled, insufficient data), 1 the
    build failed, 3 migration missing / extra missing / database down.
    """
    return _run_sync(lambda: _build_flow(json_output))


def topics_list(
    limit: int = typer.Option(50, "--limit", min=1, help="How many topics to show (max 500)"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List the built topics with sizes, labels, and keywords.

    Exit codes: 0 ok, 3 configuration error (migration missing,
    database down).
    """
    return _run_sync(lambda: _list_flow(limit, json_output))


async def _trends_flow(bins: int | None, json_output: bool) -> int:
    from corpus_kb.tools.topics_tools import topics_trends

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await topics_trends(pool, cfg, bins=bins)
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


def topics_trends(
    bins: int | None = typer.Option(
        None, "--bins", min=1, help="Date bins for the trends build (default: auto)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Per-topic frequency series over binned document dates.

    Requires analysis.topics.over_time.enabled: true. Exit codes: 0
    done (or an honest no-op: disabled, engine not built), 1 the run
    failed, 3 migration missing / database down.
    """
    return _run_sync(lambda: _trends_flow(bins, json_output))
