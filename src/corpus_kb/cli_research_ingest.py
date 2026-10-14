"""Research transcript-ingest CLI commands (zero-drift Wave 3B split).

Moved byte-identical from cli.py (the 250-line limit):

  * ``corpus-kb research ingest-transcript``
  * ``corpus-kb research ingest`` (``--watch`` drop-directory tail)

plus the honesty printers that belong to this path only: the genuine
content-detection summary, the --source-type override notice, and the
registry validator. cli_research registers them FIRST (help-output order),
and cli.py re-exports the two printers for the strategy tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from corpus_kb.cli_support import DEFAULT_TENANT, _run_async, _tenant, wired_pool

if TYPE_CHECKING:
    from pathlib import Path


def register(research_app: typer.Typer) -> None:
    """Attach the ingest commands (help order: first two under research)."""
    research_app.command("ingest-transcript")(research_ingest_transcript)
    research_app.command("ingest")(research_ingest)


def research_ingest_transcript(
    path: str = typer.Argument(..., help="Transcript file (txt|vtt|srt|csv|docx)"),
    project_id: str = typer.Option(None, "--project-id", help="Research project UUID"),
    force: bool = typer.Option(False, "--force", help="Re-ingest even if file hash unchanged"),
    source_type: str = typer.Option(
        None,
        "--source-type",
        help="Ingestion strategy override (validated against the registry)",
    ),
) -> int:
    """Ingest one transcript: turns -> roles -> exchanges -> event stream."""

    return _run_async(
        _ingest_coroutine(path, project_id, watch=False, force=force, source_type=source_type)
    )


def research_ingest(
    path: str = typer.Argument(..., help="File, directory, or glob"),
    project_id: str = typer.Option(None, "--project-id", help="Research project UUID"),
    watch: bool = typer.Option(False, "--watch", help="Tail a drop directory forever"),
    force: bool = typer.Option(False, "--force", help="Re-ingest even if file hash unchanged"),
    source_type: str = typer.Option(
        None,
        "--source-type",
        help="Ingestion strategy override (validated against the registry)",
    ),
) -> int:
    """Dynamic ingestion with separate file-hash and text-hash dedup."""
    return _run_async(
        _ingest_coroutine(path, project_id, watch=watch, force=force, source_type=source_type)
    )


def _ingest_coroutine(
    path: str,
    project_id: str | None,
    watch: bool,
    force: bool,
    source_type: str | None = None,
):
    _validate_strategy_override(source_type)
    from corpus_kb.config import load_config
    from corpus_kb.tools.research_tools import research_ingest, research_ingest_transcript

    async def _run() -> int:
        from pathlib import Path

        cfg = load_config()
        if source_type is not None:
            # The override was validated against the registry, but this path
            # never consults it — say so instead of a fake detection line.
            _print_override_notice(source_type)
        elif Path(path).is_file():
            _print_detection_summary(Path(path), cfg)
        async with wired_pool(cfg=cfg) as wired:
            pool = wired.pool
            if watch:
                from pathlib import Path

                from corpus_kb.research.dynamic_ingest import watch as watch_dir

                research_cfg = cfg.get("research", {}) or {}
                ingest_cfg = research_cfg.get("ingest", {}) or {}
                interval = int(ingest_cfg.get("watch_interval_s", 10))

                async def ingest_one(file_path: Path) -> None:
                    if source_type is None:
                        _print_detection_summary(file_path, cfg)
                    # Shared tool: ingest + the one projection catch-up.
                    await research_ingest_transcript(
                        str(file_path),
                        pool,
                        project_id=project_id,
                        tenant_id=DEFAULT_TENANT,
                    )

                await watch_dir(pool, _tenant(), Path(path), interval, ingest_one)
                return 0
            result = await research_ingest(
                path,
                pool,
                project_id=project_id,
                tenant_id=DEFAULT_TENANT,
                force=force,
            )
            import json

            print(json.dumps(result, indent=2, default=str))
            return 0 if result.get("status") == "success" else 1

    return _run()


def _validate_strategy_override(source_type: str | None) -> None:
    """Fail fast when --source-type names an unregistered ingestion strategy."""
    if source_type is None:
        return
    from corpus_kb.ingestion.registry import strategy_names

    known = strategy_names()
    if source_type not in known:
        raise typer.BadParameter(
            f"Unknown source-type strategy '{source_type}'. "
            f"Valid strategies: {', '.join(sorted(known))}",
            param_hint="--source-type",
        )


def _print_detection_summary(path: Path, cfg: dict[str, object]) -> None:
    """Print the genuine content-detection summary line, e.g.
    detected: transcript (0.80, moderator-pattern) -> strategy transcript (via detection).

    The source-type hint is the file's extension-derived document type (the
    same derivation the ingest pipeline uses) — never a hardcoded value.
    The line is informational: the research transcript pipeline ingests
    turns directly and does not consult the strategy registry.
    """
    from corpus_kb.ingestion.base import DETECT_HEAD_CHARS
    from corpus_kb.ingestion.registry import resolve_strategy
    from corpus_kb.tools.ingest_tools import _detect_source_type

    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:DETECT_HEAD_CHARS]
    except OSError:
        head = ""
    resolution = resolve_strategy(_detect_source_type(path), path.name, head, cfg, None)
    detection = resolution.detection
    confidence = detection.confidence if detection is not None else 0.0
    reason = detection.reason if detection is not None else "not attempted"
    print(
        f"detected: {resolution.detected or 'none'} ({confidence:.2f}, {reason}) "
        f"-> strategy {resolution.strategy.name} (via {resolution.basis})"
    )


def _print_override_notice(source_type: str) -> None:
    """Honest labeling for --source-type on the research ingest path.

    The override passed registry validation, but the research transcript
    pipeline ingests turns directly — the strategy registry is never
    consulted — so the notice says exactly that instead of implying the
    strategy drove the ingest.
    """
    print(
        f"strategy override {source_type}: accepted; the research transcript "
        "pipeline ingests turns directly (registry not consulted)"
    )
