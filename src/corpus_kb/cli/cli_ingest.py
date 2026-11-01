"""The ``corpus-kb ingest`` front door (Stage 5).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb ingest``         — file/dir ingest; ``--watch`` poll loop with
    auto-heal of embedding outages; ``--reembed`` repair mode.
  * ``corpus-kb ingest-status``  — read-only view of the ``ingest_runs``
    ledger + degraded counts (the same handler the MCP + HTTP surfaces call).
  * ``corpus-kb ingest-reembed`` — re-embed degraded chunks (docs-pinned
    spelling; identical handler to ``corpus-kb ingest --reembed``).
  * ``corpus-kb ingest-url``     — fetch (or crawl) a website and ingest its
    HTML pages through the web_structure strategy.

HONEST EXIT CODES (stable for scripting, documented in each command's
help): ``0`` everything ok, ``1`` partial success (some files/chunks
failed), ``2`` total failure, ``3`` configuration error (bad path, unknown
``--source-type``, unreachable database, migration missing). Every outcome
is PRINTED or emitted as ``--json`` — no silent successes, ever.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from corpus_kb.cli._plumbing import _VALIDATE_OPT, ValidateMode, validate_kwargs
from corpus_kb.cli.cli_ingest_views import (
    print_ingest_result,
    print_reembed_summary,
    print_status_report,
)
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as TENANT_DEFAULT

if TYPE_CHECKING:
    import asyncpg

# Ingest's own exit table. It is NOT the shared ``cli._plumbing`` table
# (``_plumbing.EXIT_FAILED`` there is 1). Scripts depend on these values.
# Do not renumber.
INGEST_EXIT_OK = 0
INGEST_EXIT_PARTIAL = 1
INGEST_EXIT_FAILED = 2
INGEST_EXIT_CONFIG = 3


def register(app: typer.Typer) -> None:
    """Attach the four top-level ingest commands to the main Typer app."""
    app.command("ingest")(ingest)
    app.command("ingest-status")(ingest_status)
    app.command("ingest-reembed")(ingest_reembed)
    app.command("ingest-url")(ingest_url_command)


# ---------------------------------------------------------------------------
# shared plumbing
# ---------------------------------------------------------------------------


def _finish(code: int) -> int:
    """Honest exit: typer ignores plain int returns, so non-zero must raise."""
    if code:
        raise typer.Exit(code=code)
    return INGEST_EXIT_OK


def _run_sync(coro: Callable[[], Coroutine[Any, Any, int]]) -> int:
    """asyncio.run + KeyboardInterrupt -> clean exit 0 (watch Ctrl-C)."""
    import asyncio

    import asyncpg

    try:
        return _finish(asyncio.run(coro()))
    except KeyboardInterrupt:
        print("stopped")
        raise typer.Exit(code=INGEST_EXIT_OK) from None
    except OSError as exc:
        print(f"ERROR: cannot reach the database/provider: {exc}")
        print("Fix: check the service is running, or run corpus-kb quickstart.")
        raise typer.Exit(code=INGEST_EXIT_CONFIG) from None
    except asyncpg.PostgresError as exc:
        # Bad DSN / wrong password / unknown database surface as
        # asyncpg.PostgresError subclasses, not OSError — same honest
        # config/connection bucket, exit 3.
        print(f"ERROR: cannot reach the database (check DSN/credentials): {exc}")
        print("Fix: check the DSN in config.yaml, or run corpus-kb quickstart.")
        raise typer.Exit(code=INGEST_EXIT_CONFIG) from None


def _load_config() -> dict[str, object]:
    from corpus_kb.config import load_config

    return load_config()


def _conn_str(cfg: dict[str, object]) -> str:
    db = cfg.get("database")
    db_map = db if isinstance(db, dict) else {}
    return str(db_map.get("connection_string", ""))


def _strategy_names() -> list[str]:
    from corpus_kb.ingestion.registry import strategy_names

    return strategy_names()


def _unknown_strategy_exit(source_type: str) -> None:
    from corpus_kb.ingestion.registry import strategy_names

    print(
        f"ERROR: unknown source-type strategy '{source_type}'. "
        f"Valid strategies: {', '.join(sorted(strategy_names()))}"
    )
    raise typer.Exit(code=INGEST_EXIT_CONFIG) from None


def _resolve_files(path: Path) -> list[Path]:
    """A file is itself; a directory expands to its supported files."""
    from corpus_kb.ingest_watch import WATCH_SUFFIXES

    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(
            p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in WATCH_SUFFIXES
        )
    return []


def _ingest_exit_code(results: list[dict[str, object]]) -> int:
    statuses = [str(r.get("status", "error")) for r in results]
    if statuses and all(s == "error" for s in statuses):
        return INGEST_EXIT_FAILED
    if any(s in ("error", "partial") for s in statuses):
        return INGEST_EXIT_PARTIAL
    return INGEST_EXIT_OK


async def _open_pool(cfg: dict[str, object]) -> asyncpg.Pool:
    import asyncpg

    return await asyncpg.create_pool(_conn_str(cfg))


# ---------------------------------------------------------------------------
# flows
# ---------------------------------------------------------------------------


def _watch_interval(cfg: dict[str, object]) -> float:
    """The poll interval (research.ingest.watch_interval_s; same knob the
    research watcher uses — one interval setting, not two)."""
    research = cfg.get("research")
    ingest_cfg = research.get("ingest") if isinstance(research, dict) else None
    raw = ingest_cfg.get("watch_interval_s", 10) if isinstance(ingest_cfg, dict) else 10
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 10.0


async def _ingest_files(
    path: Path, source_type: str | None, json_output: bool, validate: ValidateMode | None
) -> int:
    from corpus_kb.tools.ingest_tools import ingest_file

    cfg = _load_config()
    pool = await _open_pool(cfg)
    results: list[dict[str, object]] = []
    try:
        files = _resolve_files(path)
        if not files and not path.exists():
            print(f"ERROR: path not found: {path}")
            return INGEST_EXIT_CONFIG
        if not files:
            print(f"ERROR: no supported files under {path}")
            return INGEST_EXIT_CONFIG
        for file in files:
            result = await ingest_file(
                str(file), pool, cfg, TENANT_DEFAULT, source_type, **validate_kwargs(validate)
            )
            results.append(result)
            if not json_output:
                print_ingest_result(result)
    finally:
        await pool.close()
    if json_output:
        print(json.dumps({"results": results}, indent=2, default=str))
    return _ingest_exit_code(results)


async def _reembed_flow(limit: int | None, doc_id: str | None, json_output: bool) -> int:
    from corpus_kb.tools.ingest_surface_tools import ingest_reembed

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        summary = await ingest_reembed(pool, cfg, TENANT_DEFAULT, limit=limit, doc_id=doc_id)
    finally:
        await pool.close()
    if json_output:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print_reembed_summary(summary)
    status = str(summary.get("status", "error"))
    if status in ("unavailable", "error"):
        return INGEST_EXIT_CONFIG
    failed = summary.get("failed", 0)
    return INGEST_EXIT_PARTIAL if isinstance(failed, int) and failed else INGEST_EXIT_OK


async def _status_flow(limit: int, json_output: bool, project_id: str | None = None) -> int:
    from corpus_kb.tools.ingest_surface_tools import ingest_status

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        report = await ingest_status(pool, cfg, TENANT_DEFAULT, limit=limit, project_id=project_id)
    finally:
        await pool.close()
    if json_output:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_status_report(report)
    status = str(report.get("status"))
    return INGEST_EXIT_CONFIG if status in ("unavailable", "error") else INGEST_EXIT_OK


async def _ingest_url_flow(
    url: str,
    crawl: bool,
    max_pages: int | None,
    source_type: str | None,
    json_output: bool,
    validate: ValidateMode | None,
) -> int:
    from corpus_kb.tools.ingest_tools import ingest_url

    if source_type is not None and source_type not in _strategy_names():
        _unknown_strategy_exit(source_type)
    cfg = _load_config()
    if max_pages is not None:
        raw_web = cfg.get("web")
        web_cfg = dict(raw_web) if isinstance(raw_web, dict) else {}
        web_cfg["max_pages"] = max_pages
        cfg = {**cfg, "web": web_cfg}
    pool = await _open_pool(cfg)
    try:
        aggregate = await ingest_url(
            url,
            pool,
            cfg,
            TENANT_DEFAULT,
            crawl=crawl,
            source_type=source_type,
            **validate_kwargs(validate),
        )
    finally:
        await pool.close()
    if str(aggregate.get("status")) != "success":
        print(f"ERROR: {aggregate.get('message', 'fetch failed')}")
        return INGEST_EXIT_FAILED
    raw_pages = aggregate.get("pages", [])
    pages = (
        [page for page in raw_pages if isinstance(page, dict)]
        if isinstance(raw_pages, list)
        else []
    )
    if json_output:
        print(json.dumps(aggregate, indent=2, default=str))
    else:
        for page in pages:
            print_ingest_result(page)
        print(f"ingest-url: {aggregate.get('total_documents', 0)}/{len(pages)} pages ingested")
    statuses = [str(page.get("status", "error")) for page in pages]
    if aggregate.get("total_documents", 0) == len(pages):
        return INGEST_EXIT_OK
    all_failed = all(status == "error" for status in statuses)
    return INGEST_EXIT_FAILED if all_failed else INGEST_EXIT_PARTIAL


def ingest_url_command(
    url: str = typer.Argument(..., help="Website URL to fetch and ingest"),
    crawl: bool = typer.Option(
        False, "--crawl", help="BFS the same domain (caps: web.max_pages / web.max_depth)"
    ),
    max_pages: int | None = typer.Option(
        None, "--max-pages", min=1, help="Cap pages fetched (overrides web.max_pages)"
    ),
    source_type: str = typer.Option(
        None,
        "--source-type",
        help="Ingestion strategy override (default: web_structure via detection)",
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
    validate: ValidateMode | None = _VALIDATE_OPT,
) -> int:
    """Fetch a website and ingest its HTML pages (web_structure strategy).

    Exit codes: 0 every page ok, 1 partial success (some pages failed),
    2 total failure (fetch blocked or every page failed), 3 configuration
    error (unknown --source-type).
    """
    return _run_sync(
        lambda: _ingest_url_flow(url, crawl, max_pages, source_type, json_output, validate)
    )


async def _watch_flow(
    path: Path, source_type: str | None, no_heal: bool, validate: ValidateMode | None
) -> int:
    from corpus_kb.ingest_watch import (
        WatchLedger,
        WatchOptions,
        default_health_probe,
        ledger_available,
        watch_directory,
    )
    from corpus_kb.tools.ingest_tools import ingest_file

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        persistent = await ledger_available(pool, TENANT_DEFAULT)
        if not persistent:
            print(
                "WARNING: ingest_runs table absent (migration 022 not applied) — "
                "watch bookkeeping is IN-MEMORY ONLY (run corpus-kb setup)"
            )
        ledger = WatchLedger(persistent=persistent)
        interval = _watch_interval(cfg)

        async def ingest_one(file: Path) -> dict[str, object]:
            return await ingest_file(
                str(file), pool, cfg, TENANT_DEFAULT, source_type, **validate_kwargs(validate)
            )

        options = WatchOptions(interval_s=interval, heal=not no_heal)
        heal_label = "off" if no_heal else "on"
        print(f"watching {path} (interval {interval:g}s, auto-heal {heal_label})")
        summary = await watch_directory(
            path,
            ingest_one,
            options,
            probe=lambda: default_health_probe(cfg, pool),
            ledger=ledger,
        )
    finally:
        await pool.close()
    print(json.dumps(summary, indent=2, default=str))
    return INGEST_EXIT_OK if str(summary.get("status")) == "success" else INGEST_EXIT_PARTIAL


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def ingest(
    path: str | None = typer.Argument(None, help="File or directory to ingest"),
    source_type: str = typer.Option(
        None,
        "--source-type",
        help="Ingestion strategy override (validated against the registry)",
    ),
    watch: bool = typer.Option(
        False, "--watch", help="Poll the directory; auto-heal embedder outages"
    ),
    reembed: bool = typer.Option(
        False, "--reembed", help="Re-embed degraded (zero-vector) chunks instead of ingesting"
    ),
    limit: int | None = typer.Option(None, "--limit", help="--reembed: cap on chunks re-embedded"),
    doc_id: str | None = typer.Option(
        None, "--doc-id", help="--reembed: restrict to one document UUID"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
    no_heal: bool = typer.Option(
        False, "--no-heal", help="--watch: disable auto-heal (failures are never retried)"
    ),
    validate: ValidateMode | None = _VALIDATE_OPT,
) -> int:
    """Ingest files/directories, watch a drop folder, or re-embed degraded chunks.

    Exit codes: 0 everything ok, 1 partial success (some files failed),
    2 total failure, 3 configuration error. Ctrl-C in --watch exits 0.
    """
    if source_type is not None:
        from corpus_kb.ingestion.registry import strategy_names

        if source_type not in strategy_names():
            _unknown_strategy_exit(source_type)
    if reembed:
        return _run_sync(lambda: _reembed_flow(limit, doc_id, json_output))
    if path is None:
        print("ERROR: a PATH argument is required (or use --reembed)")
        raise typer.Exit(code=INGEST_EXIT_CONFIG) from None
    target = Path(path)
    if not target.exists():
        print(f"ERROR: path not found: {target}")
        raise typer.Exit(code=INGEST_EXIT_CONFIG) from None
    if watch:
        return _run_sync(lambda: _watch_flow(target, source_type, no_heal, validate))
    return _run_sync(lambda: _ingest_files(target, source_type, json_output, validate))


def ingest_status(
    limit: int = typer.Option(20, "--limit", min=1, help="How many recent runs to show (max 200)"),
    project_id: str | None = typer.Option(
        None, "--project-id", help="Show only the runs of this project UUID"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Read the ingest run ledger: recent runs, degraded counts, incomplete runs.

    Exit codes: 0 ok, 3 configuration error (ledger absent / bad --project-id /
    database down).
    """
    return _run_sync(lambda: _status_flow(limit, json_output, project_id))


def ingest_reembed(
    limit: int | None = typer.Option(None, "--limit", help="Cap on chunks re-embedded"),
    doc_id: str | None = typer.Option(None, "--doc-id", help="Restrict to one document UUID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Re-embed degraded (zero-vector) chunks once the provider is back.

    Exit codes: 0 ok (or nothing to do), 1 partial (some chunks failed),
    3 configuration error (migration missing, bad --doc-id, database down).
    """
    return _run_sync(lambda: _reembed_flow(limit, doc_id, json_output))
